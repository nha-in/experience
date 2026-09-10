"""Every ticket entry is mirrored into one threaded conversation with support."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from ohc_experience.support import emails
from ohc_experience.support.models import Category
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply
from ohc_experience.support.models import record_status_change
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _inbox(settings):
    settings.SUPPORT_INBOX_EMAIL = "support@ohc.network"
    settings.SUPPORT_EMAIL_DOMAIN = "experience.ohc.network"
    settings.SITE_BASE_URL = "https://hub.example.in"
    mail.outbox.clear()


@pytest.fixture
def vendor():
    return UserFactory.create(name="Meera Krishnan", email="meera@sunrise.in")


@pytest.fixture
def colleague():
    return UserFactory.create(name="Raj Menon", email="raj@sunrise.in")


@pytest.fixture
def nha_member():
    return UserFactory.create(
        name="Anand S",
        email="anand@ohc.network",
        is_nha_team=True,
    )


@pytest.fixture
def ticket(organisation, vendor) -> Ticket:
    return Ticket.objects.create(
        organisation=organisation,
        subject="Sandbox reset wiped our seeded records",
        category=Category.SANDBOX,
        priority=Priority.HIGH,
        created_by=vendor,
    )


def test_the_opening_message_reaches_support_and_copies_the_requester(ticket, vendor):
    post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)

    sent = mail.outbox[0]
    assert sent.to == ["support@ohc.network"]
    assert sent.cc == ["meera@sunrise.in"]
    assert ticket.reference in sent.subject
    assert ticket.subject in sent.subject
    assert "New support ticket" in sent.body
    assert "Our records vanished." in sent.body
    assert f"https://hub.example.in{ticket.get_absolute_url()}" in sent.body


def test_the_opening_message_owns_the_thread_anchor(ticket, vendor):
    post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)

    headers = mail.outbox[0].extra_headers
    assert headers["Message-ID"] == f"<{ticket.reference}@experience.ohc.network>"
    assert headers["X-OHC-Ticket"] == ticket.reference
    assert "In-Reply-To" not in headers


def test_later_entries_reply_to_the_anchor(ticket, vendor, nha_member):
    post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)
    message = post_reply(ticket, nha_member, "Restoring now.", from_nha_team=True)

    anchor = f"<{ticket.reference}@experience.ohc.network>"
    headers = mail.outbox[1].extra_headers
    assert headers["In-Reply-To"] == anchor
    assert headers["References"] == anchor
    assert headers["Message-ID"] == (
        f"<{ticket.reference}-{message.pk}@experience.ohc.network>"
    )
    assert "(NHA team) replied:" in mail.outbox[1].body


def test_the_gateway_gets_a_template_instead_of_headers(settings, ticket, vendor):
    """The Global Email API rejects custom headers, so drop them for that transport."""
    settings.EMAIL_BACKEND = "ohc_experience.core.mail.backends.GlobalEmailBackend"
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"support_ticket": "200001"}
    sent = []

    with patch.object(
        emails.EmailMessage,
        "send",
        autospec=True,
        side_effect=lambda self, **_: sent.append(self),
    ):
        post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)

    assert sent[0].extra_headers == {}
    assert sent[0].template_id == "200001"


def test_one_entry_sends_exactly_one_mail(ticket, vendor, colleague):
    post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)
    post_reply(ticket, colleague, "Ours too.", from_nha_team=False)

    assert [sent.to for sent in mail.outbox] == [
        ["support@ohc.network"],
        ["support@ohc.network"],
    ]


def test_a_colleague_reply_copies_the_requester_too(ticket, colleague):
    post_reply(ticket, colleague, "Ours too.", from_nha_team=False)

    assert mail.outbox[0].cc == ["meera@sunrise.in", "raj@sunrise.in"]


def test_a_vendor_reply_copies_the_nha_member_who_answered(ticket, vendor, nha_member):
    post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)
    post_reply(ticket, nha_member, "Restoring now.", from_nha_team=True)
    post_reply(ticket, vendor, "Still missing.", from_nha_team=False)

    assert mail.outbox[2].cc == ["anand@ohc.network", "meera@sunrise.in"]


def test_a_status_change_is_mirrored_too(ticket, vendor, nha_member):
    post_reply(ticket, vendor, "Our records vanished.", from_nha_team=False)
    record_status_change(ticket, nha_member, Status.RESOLVED)

    assert "changed the status to: Resolved" in mail.outbox[1].body


def test_a_mail_failure_does_not_break_the_thread(ticket, vendor):
    with patch(
        "ohc_experience.support.models.notify_support",
        side_effect=OSError("smtp down"),
    ):
        message = post_reply(
            ticket,
            vendor,
            "Our records vanished.",
            from_nha_team=False,
        )

    ticket.refresh_from_db()
    assert message.pk is not None
    assert ticket.status == Status.OPEN
    assert mail.outbox == []
