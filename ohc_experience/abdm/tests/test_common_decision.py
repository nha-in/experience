"""A product's requests are approved and rejected together, once nothing holds them."""

# ruff: noqa: F811
import re
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_wasa_lifecycle import request_renewal
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import reverify
from ohc_experience.abdm.tests.test_workflow import review_section
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.abdm.tests.test_workflow import submit_together
from ohc_experience.abdm.wasa import UNDATED_EXIT_WASA
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

QUESTION = "Which test cases cover this flow?"
HOLD = "Resolve all queries on this submission before deciding."


def product_page(environment):
    return reverse(
        "experiences:product-detail",
        args=[environment["product"].reference],
    )


def own_decision(item, action):
    return {
        "intent": "decision",
        "review_id": item.pk,
        "revision": item.selected_submission_id,
        "action": action,
        "note": "Reviewed against the evidence.",
        "reason": "Incorrect document",
    }


def raise_query(environment, item):
    workflows.raise_queries(item, environment["reviewer"], [("form", QUESTION)])
    return item.queries.get()


def holds(environment, *items, action):
    return workflows.product_decision_blockers(
        [ReviewItem.objects.get(pk=item.pk) for item in items],
        environment["admin"],
        action=action,
    )


def fresh(item):
    return ReviewItem.objects.get(pk=item.pk)


def decide_expired_renewal(environment):
    """A renewal whose certificate ran out while it waited for a decision."""
    renewal = request_renewal(environment)
    submission = renewal.selected_submission
    submission.data = {**submission.data, "wasa_valid_until": "2020-01-01"}
    type(submission).objects.filter(pk=submission.pk).update(data=submission.data)
    return renewal


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_request_takes_no_decision_of_its_own(environment, client, action):
    item = submit(environment)
    client.force_login(environment["reviewer"])

    response = client.post(product_page(environment), own_decision(item, action))

    assert response.status_code == HTTPStatus.OK
    assert b"Decide this request with Accept all or Reject all." in response.content
    assert fresh(item).pending


def test_the_page_offers_only_the_common_decision_and_queries(environment, client):
    item = submit(environment)
    client.force_login(environment["reviewer"])

    html = client.get(product_page(environment)).content.decode()
    section = review_section(html, item)

    assert 'value="approve"' not in section
    assert 'value="reject"' not in section
    assert "data-query-send-form" in section
    assert 'name="action" value="query"' in section
    assert "Accept all or Reject all" in section
    assert "Accept all</button>" in html
    assert re.search(r'name="action"\s+value="reject"', html)


def test_the_switch_opens_on_the_first_decision_that_is_free(environment, client):
    decide_expired_renewal(environment)
    client.force_login(environment["reviewer"])

    html = " ".join(client.get(product_page(environment)).content.decode().split())

    # An expired certificate cannot be accepted, but it can be sent back.
    assert "Reject all</button>" in html
    assert re.search(r'value="approve" disabled aria-describedby="bulk-hold"', html)
    assert re.search(r'value="reject" checked', html)
    assert "data-reject-controls hidden" not in html


def test_an_approver_who_cannot_query_is_pointed_at_the_common_decision(
    environment,
    client,
):
    item = submit(environment)
    approver = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=approver,
        program="abdm",
        area="review",
        category="HIE-CM",
        can_read=True,
        can_approve=True,
    )
    client.force_login(approver)

    html = client.get(product_page(environment)).content.decode()
    section = review_section(html, item)

    assert "data-query-send-form" not in section
    assert "read-only" not in section
    assert "Accept all or Reject all" in section


def test_overriding_a_recorded_request_stays_its_own_decision(environment, client):
    submit(environment)
    uhi = submit(environment, "uhi1")
    client.force_login(environment["reviewer"])

    response = client.post(
        product_page(environment),
        own_decision(uhi, "approve"),
    )

    assert response.status_code == HTTPStatus.FOUND
    assert str(list(get_messages(response.wsgi_request))[-1]) == (
        f"{uhi.title} approved."
    )
    assert fresh(uhi).status == ReviewItem.Status.APPROVED


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_an_open_query_holds_both_decisions(environment, action):
    item = submit(environment)
    raise_query(environment, item)

    with pytest.raises(ValidationError) as error:
        workflows.decide(
            fresh(item),
            environment["reviewer"],
            action=action,
            note="Reviewed against the evidence.",
            reason="Incorrect document",
        )

    assert error.value.messages == [HOLD]
    assert fresh(item).pending


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_an_answered_query_holds_both_until_it_is_resolved(environment, action):
    item = submit(environment)
    query = raise_query(environment, item)
    workflows.reply_query(query, environment["applicant"], "Cases 3 and 4.")

    assert holds(environment, item, action=action)

    workflows.resolve_query(query, environment["reviewer"])

    assert not holds(environment, item, action=action)


def test_a_query_on_one_request_holds_the_whole_common_decision(environment, client):
    m1, m2 = submit_together(environment, "m1", "m2")
    raise_query(environment, m2)
    client.force_login(environment["reviewer"])

    page = client.get(product_page(environment))
    html = " ".join(page.content.decode().split())

    assert page.context["bulk_approve_blockers"] == [f"{m2.title}: {HOLD}"]
    assert page.context["bulk_reject_blockers"] == [f"{m2.title}: {HOLD}"]
    # One reason holding both buttons is said once.
    assert "Accept all and Reject all are on hold" in html
    assert html.count(HOLD) == 1
    assert "Resolve 1 outstanding query before approving or rejecting." in html

    response = client.post(
        product_page(environment),
        {
            "intent": "bulk_decision",
            "action": "reject",
            "reviews": [
                f"{item.pk}:{item.selected_submission_id}" for item in (m1, m2)
            ],
            "note": "Reviewed against the evidence.",
            "reason_sandbox_exit_evidence": "Incorrect document",
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert fresh(m1).pending
    assert fresh(m2).pending


def test_a_query_on_wasa_holds_the_exit_beside_it(environment):
    approve(environment, "m1")
    renewal = request_renewal(environment)
    m2 = submit(environment, "m2")
    raise_query(environment, renewal)

    assert holds(environment, renewal, m2, action="approve")
    assert holds(environment, renewal, m2, action="reject")


def test_a_query_on_an_exit_holds_the_verification_beside_it(environment):
    item = submit(environment)
    verification = reverify(environment)
    raise_query(environment, item)

    assert holds(environment, verification, item, action="approve")
    assert holds(environment, verification, item, action="reject")


def test_withdrawing_a_queried_request_frees_the_rest(environment):
    m1, m2 = submit_together(environment, "m1", "m2")
    raise_query(environment, m2)
    workflows.withdraw(fresh(m2), environment["applicant"])

    assert not holds(environment, m1, action="approve")


def test_a_reviewer_closes_a_query_nobody_answered(environment, client):
    item = submit(environment)
    query = raise_query(environment, item)
    assert fresh(item).status == ReviewItem.Status.QUERY
    client.force_login(environment["reviewer"])

    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"intent": "close", "return_to_product": "1"},
    )

    assert response.url == product_page(environment) + f"#review-{item.pk}"
    assert str(list(get_messages(response.wsgi_request))[-1]) == "Query closed."
    query.refresh_from_db()
    assert query.status == "resolved"
    assert query.resolved_at
    assert not query.reply
    item = fresh(item)
    assert item.status == ReviewItem.Status.IN_REVIEW
    assert item.application.status == "under_review"
    assert item.history.filter(action="Query closed").exists()
    assert not holds(environment, item, action="reject")


def test_closing_one_query_leaves_the_request_with_the_others(environment):
    item = submit(environment)
    workflows.raise_queries(
        item,
        environment["reviewer"],
        [("form", QUESTION), ("start_date", "Why does testing start here?")],
    )
    first, _second = fresh(item).queries.all()

    workflows.close_query(first, environment["reviewer"])

    assert fresh(item).status == ReviewItem.Status.QUERY


def test_only_an_unanswered_query_on_the_current_review_closes(environment):
    item = submit(environment)
    query = raise_query(environment, item)
    workflows.reply_query(query, environment["applicant"], "Cases 3 and 4.")

    with pytest.raises(ValidationError, match="Only an unanswered query"):
        workflows.close_query(query, environment["reviewer"])


def test_an_integrator_cannot_close_a_query(environment):
    query = raise_query(environment, submit(environment))

    with pytest.raises(PermissionDenied):
        workflows.close_query(query, environment["applicant"])


def test_close_query_is_offered_to_reviewers_and_not_to_integrators(
    environment,
    client,
):
    item = submit(environment)
    raise_query(environment, item)
    client.force_login(environment["reviewer"])
    assert ">Close query</button>" in review_section(
        client.get(product_page(environment)).content.decode(),
        item,
    )

    client.force_login(environment["applicant"])
    html = client.get(
        reverse(
            "experiences:track",
            args=[environment["product"].reference, "HIE-CM"],
            query={"milestone": "m1"},
        ),
    ).content.decode()

    assert "Your reply" in html
    assert "Close query" not in html


def test_a_closed_query_reads_as_closed(environment, client):
    item = submit(environment)
    query = raise_query(environment, item)
    workflows.close_query(query, environment["reviewer"])
    client.force_login(environment["reviewer"])

    section = review_section(
        client.get(product_page(environment)).content.decode(),
        item,
    )

    assert "Closed " in section
    assert "Close query" not in section
    assert milestone(environment, "m1").pending


def undated(item):
    """An exit form whose WASA has no expiry date, as forms migrated from the
    legacy portal can."""
    submission = item.selected_submission
    data = {**submission.data, "wasa_valid_until": None}
    type(submission).objects.filter(pk=submission.pk).update(data=data)
    return fresh(item)


def accept_all(environment, *items):
    return workflows.decide_product(
        environment["product"],
        environment["admin"],
        action="approve",
        expected_revisions={
            str(item.pk): str(item.selected_submission_id) for item in items
        },
        note="Reviewed against the evidence.",
    )


def wasa_sources(environment):
    """The requests each of the product's WASA approvals came from."""
    return list(
        ProductOutcome.objects.filter(
            product=environment["product"],
            outcome_type="wasa_approval",
        ).values_list("source_application_id", flat=True),
    )


def test_an_undated_exit_wasa_holds_accept_all_with_nothing_to_stand_in(
    environment,
):
    m1 = undated(submit(environment))

    assert holds(environment, m1, action="approve") == [
        f"{m1.title}: {UNDATED_EXIT_WASA}",
    ]
    assert not holds(environment, m1, action="reject")


def test_a_wasa_review_accepted_alongside_stands_in_for_an_undated_exit(
    environment,
):
    m1 = undated(submit(environment))
    renewal = request_renewal(environment)
    assert holds(environment, m1, action="approve")
    assert not holds(environment, m1, renewal, action="approve")

    accept_all(environment, m1, renewal)

    assert fresh(m1).status == ReviewItem.Status.APPROVED
    assert fresh(renewal).status == ReviewItem.Status.APPROVED
    # The exit form records no undated WASA of its own.
    assert wasa_sources(environment) == [renewal.application_id]


def test_a_valid_product_wasa_stands_in_for_an_undated_exit(environment):
    m1 = approve(environment, "m1")
    m2 = undated(submit(environment, "m2"))
    assert not holds(environment, m2, action="approve")

    accept_all(environment, m2)

    assert fresh(m2).status == ReviewItem.Status.APPROVED
    assert wasa_sources(environment) == [m1.application_id]


def test_an_expired_product_wasa_does_not_stand_in_for_an_undated_exit(
    environment,
):
    approve(environment, "m1")
    ProductOutcome.objects.filter(outcome_type="wasa_approval").update(
        valid_until=timezone.localdate() - timedelta(days=1),
    )
    m2 = undated(submit(environment, "m2"))

    assert holds(environment, m2, action="approve") == [
        f"{m2.title}: {UNDATED_EXIT_WASA}",
    ]
