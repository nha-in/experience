from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import legacy
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ReviewItem

pytestmark = pytest.mark.django_db


def test_progress_counts_shared_m1_only_once(client, environment):  # noqa: F811
    approve(environment)
    client.force_login(environment["applicant"])
    response = client.get(environment["product"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert ABDM.milestones_docs_url.encode() in response.content
    # HIE-CM shows M1-M4 and the UHI track shows its milestone over M1 and M2, so M1
    # counts once.
    assert response.context["progress"] == {
        "total": 5,
        "approved": 1,
        "active_tracks": 2,
        "awaiting_review": 0,
    }


def test_next_step_opens_an_available_milestone(client, environment):  # noqa: F811
    client.force_login(environment["applicant"])
    response = client.get(environment["product"].get_absolute_url())
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue milestone"
    assert next_step["url"].endswith("/tracks/HIE-CM/?milestone=m1")
    assert response.context["progress"]["approved"] == 0


def test_query_is_prioritised_only_until_the_integrator_replies(
    client,
    environment,  # noqa: F811
):
    item = submit(environment)
    workflows.assign_review(item, environment["admin"], environment["reviewer"])
    workflows.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Clarify evidence",
    )
    client.force_login(environment["applicant"])
    url = environment["product"].get_absolute_url()
    response = client.get(url)
    assert response.context["next_step"]["action"] == "Respond to query"
    assert response.context["next_step"]["url"].endswith("?milestone=m1#queries")
    assert response.context["tracks"][0]["tiles"][0]["reply_needed"] is True
    workflows.reply_query(item.queries.get(), environment["applicant"], "Clarified")
    response = client.get(url)
    assert response.context["next_step"]["action"] != "Respond to query"
    assert response.context["tracks"][0]["tiles"][0]["reply_needed"] is False


def test_another_products_query_does_not_replace_this_products_guidance(
    client,
    environment,  # noqa: F811
):
    item = submit(environment)
    workflows.assign_review(item, environment["admin"], environment["reviewer"])
    workflows.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Clarify evidence",
    )
    second, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert second, form.errors
    client.force_login(environment["applicant"])
    response = client.get(second.get_absolute_url())
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue milestone"
    assert next_step["url"] == (
        reverse("experiences:track", args=[second.reference, "HIE-CM"])
        + "?milestone=m1"
    )


def test_reviewer_does_not_receive_integrator_actions(client, environment):  # noqa: F811
    client.force_login(environment["reviewer"])
    response = client.get(environment["product"].get_absolute_url(), follow=True)
    assert response.redirect_chain == [
        (
            reverse(
                "experiences:product-detail",
                args=[environment["product"].reference],
            ),
            HTTPStatus.FOUND,
        ),
    ]
    assert response.status_code == HTTPStatus.OK
    assert "next_step" not in response.context
    assert b"Recommended next step" not in response.content


def test_withdrawn_organisation_guidance_requires_resubmission(
    client,
    environment,  # noqa: F811
):
    item = workflows.organisation_review(
        environment["org"],
        environment["applicant"],
    )
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=organisation_data(),
        submit=True,
    )
    assert saved, form.errors
    workflows.withdraw(item, environment["applicant"])
    client.force_login(environment["applicant"])
    response = client.get(environment["product"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue verification"
    assert next_step["url"] == reverse("experiences:organisation")


def test_verification_under_review_still_leads_to_milestone_evidence(
    client,
    environment,  # noqa: F811
):
    """Milestones no longer wait for verification, so neither does the guidance."""
    item = workflows.organisation_review(
        environment["org"],
        environment["applicant"],
    )
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=organisation_data(),
        submit=True,
    )
    assert saved, form.errors
    client.force_login(environment["applicant"])
    response = client.get(environment["product"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue milestone"
    assert next_step["url"].endswith("/tracks/HIE-CM/?milestone=m1")


def test_review_guidance_skips_approved_milestones(client, environment):  # noqa: F811
    approve(environment)
    # P3 waits on the two phases before it, so they are decided, not just sent.
    for key in ("p1", "p2"):
        approve(environment, key)
    for key in ("m2", "m3", "m4", "p3", "p4", "uhi1"):
        submit(environment, key)
    client.force_login(environment["applicant"])
    response = client.get(environment["product"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["action"] == "View current request"
    assert next_step["url"].endswith("/tracks/HIE-CM/?milestone=m2")


def test_the_overview_asks_first_for_what_the_import_left_to_confirm(
    client,
    environment,  # noqa: F811
):
    product = environment["product"]
    product.metadata = {legacy.LEGACY_GAPS: [legacy.SOLUTION_TYPE, legacy.TRACKS]}
    product.save(update_fields=["metadata"])
    client.force_login(environment["applicant"])
    response = client.get(product.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["title"] == "Confirm your product details"
    assert next_step["tone"] == "warning"
    assert "left 2 details to confirm" in next_step["detail"]
    assert next_step["url"].endswith("/edit/")


def test_a_rejected_registration_is_not_asked_to_confirm_its_gaps(
    client,
    environment,  # noqa: F811
):
    product = environment["product"]
    product.metadata = {legacy.LEGACY_GAPS: [legacy.SOLUTION_TYPE]}
    product.save(update_fields=["metadata"])
    registration = product.registration
    registration.status = ReviewItem.Status.REJECTED
    registration.save(update_fields=["status"])
    client.force_login(environment["applicant"])

    html = client.get(product.get_absolute_url()).content.decode()

    assert "left 1 detail to confirm" not in html
    assert "Product registration was rejected" in html
