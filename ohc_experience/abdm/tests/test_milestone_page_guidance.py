"""What a milestone page says around the form: its documentation, which saved
version is on screen, how long its history runs, and where to go once it is
approved."""

import re

import pytest
from django.urls import reverse

from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import workflows

pytestmark = pytest.mark.django_db


def page(client, environment, key="m1"):  # noqa: F811
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "ABDM"],
    )
    return client.get(url, {"milestone": key}).content.decode()


def text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_track_offers_its_documentation_as_a_button(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])

    html = page(client, environment)

    docs = str(TRACK_MAP["ABDM"].docs_url)
    assert re.search(
        rf'<a class="ui-btn ui-btn--outline [^"]*"\s+href="{re.escape(docs)}"',
        html,
    )


def test_saved_evidence_names_its_version(
    environment,  # noqa: F811
    client,
):
    item = milestone(environment)
    workflows.save_review_form(item, environment["applicant"], data=evidence_data())
    client.force_login(environment["applicant"])

    html = page(client, environment)

    saved = text(re.search(r"Saved evidence.*?</div>", html, re.S).group(0))
    assert re.search(r"Version 1 · \d{2}/\d{2}/\d{4}, \d{2}:\d{2} IST", saved)
    assert "Revision" not in saved
    assert "Submission 1" not in saved

    item.refresh_from_db()
    workflows.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
        files=files(),
    )
    html = page(client, environment)

    saved = text(re.search(r"Saved evidence.*?</div>", html, re.S).group(0))
    assert saved.startswith("Saved evidence Version 2 ·")
