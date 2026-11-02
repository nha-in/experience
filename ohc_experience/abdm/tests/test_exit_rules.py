"""What an integrator can send for review, and when."""

# ruff: noqa: F811
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_workflow import _pending_organisation
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import claims_exit
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import ready
from ohc_experience.abdm.tests.test_workflow import reverify
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.abdm.tests.test_workflow import submit_together
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ReviewItem

pytestmark = pytest.mark.django_db

M1_OPEN = (
    "M1 is under review. Withdraw it to send this milestone with it, or submit "
    "this once it is decided."
)
M1_AND_M2_OPEN = (
    "M1 and M2 are under review. Withdraw them to send this milestone with them, "
    "or submit this once they are decided."
)
UNVERIFIED = "Submit this once your organisation is verified."


def track(environment, key, code="HIE-CM"):
    return reverse(
        "experiences:track",
        args=[environment["product"].reference, code],
        query={"milestone": key},
    )


def save_draft(environment, key):
    _item, form, saved = workflows.save_review_form(
        milestone(environment, key),
        environment["applicant"],
        data=evidence_data(),
        files=files(),
    )
    assert saved, form.errors


def refused(environment, key, reason):
    with pytest.raises(ValidationError) as error:
        submit(environment, key)
    assert error.value.messages == [reason]


def test_a_second_exit_waits_for_the_one_that_is_open(environment):
    submit(environment, "m1")

    refused(environment, "m2", M1_OPEN)
    assert milestone(environment, "m2").status == ReviewItem.Status.DRAFT


def test_it_names_everything_that_is_open(environment):
    submit_together(environment, "m1", "m2")

    refused(environment, "m3", M1_AND_M2_OPEN)


def test_a_query_keeps_the_exit_open(environment):
    item = submit(environment, "m1")
    workflows.raise_queries(
        item,
        environment["reviewer"],
        [("form", "Which test cases cover this flow?")],
    )

    refused(environment, "m2", M1_OPEN)


def test_a_waiting_milestone_still_saves_its_draft(environment):
    submit(environment, "m1")

    save_draft(environment, "m2")

    assert milestone(environment, "m2").selected_submission_id


def test_several_milestones_can_be_sent_as_one_exit(environment):
    items = submit_together(environment, "m1", "m2", "m3")

    assert all(item.pending for item in items)


def test_a_decided_exit_frees_the_product_for_the_next(environment):
    approve(environment, "m1")

    assert submit(environment, "m2").pending


def test_a_rejected_exit_is_not_open(environment):
    approve(environment, "m1")
    item = submit(environment, "m2")
    workflows.decide(
        item,
        environment["reviewer"],
        action="reject",
        reason="Incorrect document",
    )

    assert submit(environment, "m3").pending


def test_withdrawing_lets_the_exit_come_back_with_another_milestone(environment):
    m1, m2 = submit_together(environment, "m1", "m2")
    references = m1.application.reference, m2.application.reference

    workflows.withdraw(m2, environment["applicant"])
    workflows.withdraw(m1, environment["applicant"])
    m1, m2, m3 = submit_together(environment, "m1", "m2", "m3")

    assert all(item.pending for item in (m1, m2, m3))
    assert (m1.application.reference, m2.application.reference) == references
    assert (m1.resubmission_count, m2.resubmission_count) == (1, 1)
    assert m3.resubmission_count == 0


def test_a_withdrawn_milestone_comes_back_beside_what_is_still_open(environment):
    _m1, m2 = submit_together(environment, "m1", "m2")
    workflows.withdraw(m2, environment["applicant"])

    assert submit(environment, "m2").pending


def test_a_rejected_milestone_comes_back_beside_what_is_still_open(environment):
    """M2 open on a rejected M1 could never be decided if M1 had to wait for it."""
    m1, _m2 = submit_together(environment, "m1", "m2")
    ReviewItem.objects.filter(pk=m1.pk).update(status=ReviewItem.Status.REJECTED)

    assert submit(environment, "m1").pending


def test_an_exit_elsewhere_holds_nothing_here(environment):
    claims_exit(environment, "m1")

    assert submit(environment, "m1").pending


def test_uhi_is_no_exit_and_waits_on_nothing_open(environment):
    submit(environment, "m1")

    assert submit(environment, "uhi1").pending


def test_an_open_renewal_is_no_exit(environment):
    item = workflows.certification_review(
        environment["product"],
        environment["applicant"],
    )
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=certificate_data(),
        files={"wasa_certificate": pdf("wasa.pdf")},
        submit=True,
    )
    assert saved, form.errors

    assert submit(environment, "m1").pending


def test_the_track_page_says_why_and_offers_only_a_draft(environment, client):
    submit(environment, "m1")
    client.force_login(environment["applicant"])

    response = client.get(track(environment, "m2"))
    html = response.content.decode()

    assert M1_OPEN in html
    assert 'data-submit-blocked="true"' in html
    assert 'value="draft"' in html
    # Nothing to send it with until the exit is back.
    assert "submission_choices" not in response.context
    assert "Milestone locked" not in html


def test_a_page_that_can_send_marks_what_cannot_join(environment, client):
    _m1, m2 = submit_together(environment, "m1", "m2")
    workflows.withdraw(m2, environment["applicant"])
    client.force_login(environment["applicant"])

    response = client.get(track(environment, "m2"))

    assert {
        choice["code"]: choice["blocked"]
        for choice in response.context["submission_choices"]
    } == {"M3": M1_OPEN, "M4": M1_OPEN}


def test_guidance_does_not_point_at_a_milestone_that_waits(environment, client):
    submit(environment, "m1")
    client.force_login(environment["applicant"])

    step = client.get(environment["product"].get_absolute_url()).context["next_step"]

    # M2 waits for M1. UHI is no exit, so it can go ahead.
    assert step["action"] == "Continue milestone"
    assert step["url"].endswith("?milestone=uhi1")


def test_guidance_follows_the_exit_when_nothing_else_can_go(environment, client):
    submit(environment, "m1")
    submit(environment, "uhi1")
    client.force_login(environment["applicant"])

    step = client.get(environment["product"].get_absolute_url()).context["next_step"]

    assert step["action"] == "View current request"
    assert step["url"].endswith("?milestone=m1")


def test_guidance_moves_on_once_the_exit_is_decided(environment, client):
    approve(environment, "m1")
    client.force_login(environment["applicant"])

    step = client.get(environment["product"].get_absolute_url()).context["next_step"]

    assert step["action"] == "Continue milestone"
    assert step["url"].endswith("?milestone=m2")


@pytest.mark.parametrize("key", ["m1", "uhi1"])
def test_no_milestone_is_sent_before_verification(environment, key):
    if key == "uhi1":
        submit(environment, "m1")
    reverify(environment)

    refused(environment, key, UNVERIFIED)


def test_a_rejected_exit_cannot_come_back_before_verification(environment):
    item = submit(environment, "m1")
    workflows.decide(
        item,
        environment["reviewer"],
        action="reject",
        reason="Incorrect document",
    )
    reverify(environment)

    refused(environment, "m1", UNVERIFIED)


def test_a_draft_still_saves_before_verification(environment):
    reverify(environment)

    save_draft(environment, "m1")

    assert milestone(environment, "m1").selected_submission_id


def test_an_exit_already_open_stays_open_through_verification(environment):
    submit(environment, "m1")
    reverify(environment)

    assert milestone(environment, "m1").pending


def test_a_new_product_waits_for_verification_with_no_credentials(environment):
    _org, product = _pending_organisation(environment)
    claims = {**environment, "product": product}
    assert not ProductCredential.objects.filter(product=product).exists()

    item = product.milestones.get(key="m1").application.review_item
    with pytest.raises(ValidationError) as error:
        workflows.save_review_form(
            item,
            environment["applicant"],
            data=evidence_data(),
            files=files(),
            submit=True,
        )
    assert error.value.messages == [UNVERIFIED]
    assert claims["product"].milestones.get(key="m1").application.review_item.editable


def test_a_new_product_of_a_verified_organisation_can_send_its_own_exit(environment):
    submit(environment, "m1")
    product, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert product, form.errors
    ready(product)

    assert submit({**environment, "product": product}, "m1").pending


def test_the_track_page_explains_the_wait_for_verification(environment, client):
    reverify(environment)
    client.force_login(environment["applicant"])

    html = client.get(track(environment, "m1")).content.decode()

    assert UNVERIFIED in html
    assert 'data-submit-blocked="true"' in html


def test_wasa_waits_for_verification_too(environment):
    reverify(environment)
    item = workflows.certification_review(
        environment["product"],
        environment["applicant"],
    )

    with pytest.raises(ValidationError) as error:
        workflows.save_review_form(
            item,
            environment["applicant"],
            data=certificate_data(),
            files={"wasa_certificate": pdf("wasa.pdf")},
            submit=True,
        )

    assert error.value.messages == [UNVERIFIED]


def test_the_wasa_page_holds_a_first_submission_back(environment, client):
    reverify(environment)
    client.force_login(environment["applicant"])

    html = client.get(
        reverse(
            "experiences:product-certification",
            args=[environment["product"].reference],
        ),
    ).content.decode()

    assert UNVERIFIED in html
    assert "data-request-submit" not in html
    assert 'value="draft"' in html
