from __future__ import annotations

import logging

import pytest
from django.contrib.sites.models import Site

from ohc_experience.core.mail import absolute_url
from ohc_experience.core.mail import deliver
from ohc_experience.core.mail import team_recipients
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_deliver_sends_and_never_raises(mailoutbox, monkeypatch, caplog):
    deliver("Hello", "Body", ["one@example.in"])
    assert [message.subject for message in mailoutbox] == ["Hello"]

    def refuse(*args, **kwargs):
        msg = "nodename nor servname provided"
        raise OSError(msg)

    monkeypatch.setattr("ohc_experience.core.mail.send_mail", refuse)
    with caplog.at_level(logging.ERROR, logger="ohc_experience.core.mail"):
        deliver("Again", "Body", ["one@example.in"])

    assert "Could not send 'Again'" in caplog.text


def test_absolute_url_follows_the_site_domain_and_debug(settings):
    site = Site.objects.get_current()
    site.domain = "portal.example.in"
    site.save()
    Site.objects.clear_cache()

    settings.DEBUG = False
    assert absolute_url("/assess/queue/") == "https://portal.example.in/assess/queue/"
    settings.DEBUG = True
    assert absolute_url("/") == "http://portal.example.in/"


def test_team_recipients_prefer_the_inbox_then_every_active_team_member(settings):
    UserFactory(email="zed@ohc.network", is_ohc_team=True)
    UserFactory(email="amy@ohc.network", is_ohc_team=True)
    UserFactory(email="gone@ohc.network", is_ohc_team=True, is_active=False)
    UserFactory(email="vendor@example.in")

    settings.ABDM_REVIEW_INBOX = ""
    assert team_recipients() == ["amy@ohc.network", "zed@ohc.network"]

    settings.ABDM_REVIEW_INBOX = "desk@ohc.network"
    assert team_recipients() == ["desk@ohc.network"]
