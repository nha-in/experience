"""What a milestone page says around the form: its documentation, which saved
version is on screen, how long its history runs, and where to go once it is
approved."""

import re

import pytest
from django.urls import reverse

from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401

pytestmark = pytest.mark.django_db


def page(client, environment, key="m1"):  # noqa: F811
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "ABDM"],
    )
    return client.get(url, {"milestone": key}).content.decode()


def test_the_track_offers_its_documentation_as_a_button(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])

    html = page(client, environment)

    docs = str(TRACK_MAP["ABDM"].docs_url)
    assert re.search(
        rf'<a class="ui-btn ui-btn--outline [^"]*"\s+href="{re.escape(docs)}"',
        html,
    )
