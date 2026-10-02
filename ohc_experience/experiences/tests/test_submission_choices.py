"""What each track page offers to submit alongside the milestone it shows."""

# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import nhcx_product
from ohc_experience.abdm.tests.test_workflow import phr_product
from ohc_experience.abdm.tests.test_workflow import product_data
from ohc_experience.abdm.tests.test_workflow import ready
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows

pytestmark = pytest.mark.django_db


def offered(client, environment, key, code="ABDM"):
    """Every milestone the page lists, mapped to the reason it cannot join."""
    response = client.get(
        reverse("experiences:track", args=[environment["product"].reference, code])
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
    locker = {**environment, "product": phr_product(environment)}
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


def test_nhcx_offers_its_role_with_the_milestones_it_builds_on(environment, client):
    """Legacy took NHCX on the same exit form as M1: a payer joins M1 and M3.

    A payer's track shows M1 and M3, never the provider's M2.
    """
    claims = {**environment, "product": nhcx_product(environment)}
    client.force_login(environment["applicant"])

    assert offered(client, claims, "m1", "NHCX") == {"M3": "", "Payer": ""}
    assert offered(client, claims, "nhcx_payer", "NHCX") == {"M1": "", "M3": ""}
    # The ABDM page shows no NHCX role, so it offers none.
    assert offered(client, claims, "m1") == {"M2": "", "M3": "", "M4": ""}
    submit(claims, "m1")
    assert offered(client, claims, "m3", "NHCX") == {"Payer": ""}
    submit(claims, "m3")
    assert offered(client, claims, "nhcx_payer", "NHCX") == {}


def test_a_patient_app_joins_the_phr_milestone_it_builds_on(environment, client):
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={
            **product_data("Claims companion"),
            "solution_type": ["phr"],
            "applied_milestones": ["PHR:p1", "NHCX:nhcx_patient_app"],
        },
    )
    assert product, form.errors
    ready(product)
    app = {**environment, "product": product}
    client.force_login(environment["applicant"])

    assert offered(client, app, "p1", "NHCX") == {"Patient app": ""}
    assert offered(client, app, "nhcx_patient_app", "NHCX") == {"P1": ""}
    assert offered(client, app, "p1", "PHR") == {"P2": "", "P3": ""}


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
            product=environment["product"],
            application__milestone__key="m2",
        ),
        environment["applicant"],
    )
    workflows.withdraw(
        workflows.ReviewItem.objects.get(
            product=environment["product"],
            application__milestone__key="m1",
        ),
        environment["applicant"],
    )
    client.force_login(environment["applicant"])

    assert offered(client, environment, "m1") == {"M2": "", "M3": "", "M4": ""}
    assert offered(client, environment, "m1", "UHI") == {"M2": ""}
