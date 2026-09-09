from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows

pytestmark = pytest.mark.django_db


def test_progress_counts_shared_m1_only_once(client, environment):  # noqa: F811
    approve(environment)
    client.force_login(environment["applicant"])
    response = client.get(environment["workspace"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert response.context["progress"] == {
        "total": 7,
        "approved": 1,
        "active_tracks": 4,
        "awaiting_review": 0,
    }


def test_next_step_opens_an_available_milestone(client, environment):  # noqa: F811
    client.force_login(environment["applicant"])
    response = client.get(environment["workspace"].get_absolute_url())
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue milestone"
    assert next_step["url"].endswith("/tracks/HI-CM/?milestone=m1")
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
    url = environment["workspace"].get_absolute_url()
    response = client.get(url)
    assert response.context["next_step"]["action"] == "Respond to query"
    assert response.context["next_step"]["url"].endswith("?milestone=m1#queries")
    workflows.reply_query(item.queries.get(), environment["applicant"], "Clarified")
    response = client.get(url)
    assert response.context["next_step"]["action"] != "Respond to query"


def test_another_products_query_does_not_replace_registration_guidance(
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
    assert next_step["action"] == "View registration"
    assert next_step["url"] == reverse(
        "experiences:product-edit",
        args=[second.reference],
    )


def test_reviewer_does_not_receive_integrator_actions(client, environment):  # noqa: F811
    client.force_login(environment["reviewer"])
    response = client.get(environment["workspace"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert response.context["next_step"] is None


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
    response = client.get(environment["workspace"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue organisation"
    assert next_step["url"] == reverse("experiences:organisation")


def test_withdrawn_registration_guidance_precedes_milestone_evidence(
    client,
    environment,  # noqa: F811
):
    workspace = environment["workspace"]
    item = workspace.product.review_items.get(kind="product_registration")
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=product_data(),
        submit=True,
    )
    assert saved, form.errors
    workflows.withdraw(item, environment["applicant"])
    client.force_login(environment["applicant"])
    response = client.get(workspace.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["action"] == "Continue registration"
    assert next_step["url"] == reverse(
        "experiences:product-edit",
        args=[workspace.reference],
    )


def test_review_guidance_skips_approved_milestones(client, environment):  # noqa: F811
    approve(environment)
    for key in ("m2", "phr1", "locker1", "uhi1"):
        submit(environment, key)
    client.force_login(environment["applicant"])
    response = client.get(environment["workspace"].get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    next_step = response.context["next_step"]
    assert next_step["action"] == "View current request"
    assert next_step["url"].endswith("/tracks/HI-CM/?milestone=m2")
