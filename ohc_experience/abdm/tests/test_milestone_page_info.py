"""The (i) on a milestone page says what the milestone is and links its docs.

The product form already carries this beside every track and milestone it
offers. The milestone page is where the work happens, so it carries the same
sentence and the milestone's own documentation page.
"""

import re
from html import unescape

import pytest
from django.urls import reverse
from django.utils.html import escape

from ohc_experience.abdm.catalog import MILESTONES
from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401

pytestmark = pytest.mark.django_db


def documented(response):
    """`{label: docs url}` for every (i) panel on the page."""
    return {
        unescape(label): url
        for label, url in re.findall(
            r'aria-label="About ([^"]+)".+?href="([^"]+)"[^>]*>Open documentation',
            response.content.decode(),
            re.S,
        )
    }


@pytest.mark.parametrize("key", TRACK_MAP["ABDM"].keys)
def test_milestone_header_describes_the_milestone_and_links_its_docs(
    client,
    environment,  # noqa: F811
    key,
):
    client.force_login(environment["applicant"])
    milestone = MILESTONES[key]

    response = client.get(
        reverse("experiences:track", args=[environment["product"].reference, "ABDM"])
        + f"?milestone={key}",
    )

    assert f'id="info-milestone-{key}"'.encode() in response.content
    assert escape(milestone.description).encode() in response.content
    # The milestone's own page, not the track's documentation on every milestone.
    assert documented(response)[milestone.name] == milestone.docs_url
    assert milestone.docs_url != TRACK_MAP["ABDM"].docs_url
