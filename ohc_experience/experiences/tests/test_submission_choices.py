"""What each track page offers to submit alongside the milestone it shows."""

# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import nhcx_workspace
from ohc_experience.abdm.tests.test_workflow import phr_workspace
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows

pytestmark = pytest.mark.django_db


def offered(client, environment, key, code="ABDM"):
    """Every milestone the page lists, mapped to the reason it cannot join."""
    response = client.get(
        reverse("experiences:track", args=[environment["workspace"].reference, code])
        + f"?milestone={key}",
    )
    assert response.status_code == HTTPStatus.OK
    return {
        choice["code"]: choice["blocked"]
        for choice in response.context.get("submission_choices", [])
    }


def test_abdm_offers_the_rest_of_the_track_and_shrinks_as_each_is_submitted(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    assert offered(client, environment, "m1") == {"M2": "", "M3": "", "M4": ""}
    submit(environment, "m1")
    assert offered(client, environment, "m2") == {"M3": "", "M4": ""}
    submit(environment, "m2")
    assert offered(client, environment, "m3") == {"M4": ""}
    submit(environment, "m3")
    assert offered(client, environment, "m4") == {}


def test_phr_offers_the_rest_of_the_track_and_shrinks_as_each_is_submitted(
    environment,
    client,
):
    locker = {**environment, "workspace": phr_workspace(environment)}
    client.force_login(environment["applicant"])

    assert offered(client, locker, "p1", "PHR") == {"P2": "", "P3": "", "P4": ""}
    submit(environment, "p1")
    assert offered(client, locker, "p2", "PHR") == {"P3": "", "P4": ""}
    submit(environment, "p2")
    assert offered(client, locker, "p3", "PHR") == {"P4": ""}
    submit(environment, "p3")
    assert offered(client, locker, "p4", "PHR") == {}


def test_uhi_offers_only_what_shares_this_evidence(environment, client):
    """UHI answers its own form, so no submission of M1's evidence covers it."""
    client.force_login(environment["applicant"])

    assert offered(client, environment, "m1", "UHI") == {"M2": ""}
    submit(environment, "m1")
    assert offered(client, environment, "m2", "UHI") == {}


def test_nhcx_offers_nothing_beside_the_identity_milestone(environment, client):
    """NHCX1 shares the evidence form but not the evidence: another scope."""
    claims = {**environment, "workspace": nhcx_workspace(environment)}
    client.force_login(environment["applicant"])

    assert offered(client, claims, "m1", "NHCX") == {}
    assert offered(client, claims, "nhcx1", "NHCX") == {}


def test_a_milestone_submitted_once_can_be_resubmitted_with_the_batch(
    environment,
    client,
):
    """Teleconsult's shape: M1 and M2 both sent back, both open again.

    Choosing M2 here means submitting M1's evidence for it, over the revision
    M2 already holds.
    """
    submit(environment, "m1")
    submit(environment, "m2")
    workflows.withdraw(
        workflows.ReviewItem.objects.get(
            product=environment["workspace"].product,
            application__milestone__key="m2",
        ),
        environment["applicant"],
    )
    workflows.withdraw(
        workflows.ReviewItem.objects.get(
            product=environment["workspace"].product,
            application__milestone__key="m1",
        ),
        environment["applicant"],
    )
    client.force_login(environment["applicant"])

    assert offered(client, environment, "m1") == {"M2": "", "M3": "", "M4": ""}
    assert offered(client, environment, "m1", "UHI") == {"M2": ""}
