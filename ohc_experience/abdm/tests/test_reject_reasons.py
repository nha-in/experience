"""A reviewer rejects a request with one reason from the form's list."""

from http import HTTPStatus

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.definitions import ExitEvidence
from ohc_experience.abdm.definitions import OrganisationVerification
from ohc_experience.abdm.tests import test_workflow as workflow_fixtures
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.workflows import OTHER_REASON

pytestmark = pytest.mark.django_db
environment = workflow_fixtures.environment

DOCUMENTS = "Incorrect document"
#: Short enough to read at a glance.
MOST_REASONS = 20


def test_each_list_is_short_distinct_and_ends_with_other():
    for form in (OrganisationVerification, ExitEvidence):
        reasons = form.reject_reasons
        assert len(set(reasons)) == len(reasons), form.key
        assert OTHER_REASON not in reasons, form.key
        assert services.reject_reasons(form)[-1] == OTHER_REASON
        assert len(services.reject_reasons(form)) <= MOST_REASONS, form.key

    # A form nobody decides offers nothing to choose from.
    for application in ABDM.applications.all():
        for form in application.forms:
            if form.auto_approve:
                assert not form.reject_reasons, form.key


def test_the_reason_reaches_the_integrator_beside_the_note(environment, client):
    item = submit(environment)
    client.force_login(environment["reviewer"])
    url = item.get_absolute_url()
    page = client.get(url)
    for reason in services.reject_reasons(ExitEvidence):
        assert f'value="{reason}"'.encode() in page.content

    response = client.post(
        url,
        {
            "action": "reject",
            "reason": DOCUMENTS,
            "note": "The FT certificate covers M1 only.",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    item.refresh_from_db()
    assert item.status == "rejected"
    assert item.decision_reason == DOCUMENTS
    assert item.decision_note == "The FT certificate covers M1 only."
    event = item.history.get(action="Rejected")
    assert event.detail["reason"] == DOCUMENTS
    (notice,) = [
        sent for sent in mail.outbox if "Rejected, changes needed" in sent.body
    ]
    assert f"Reason: {DOCUMENTS}\n" in notice.body
    assert "The FT certificate covers M1 only." in notice.body
    client.force_login(environment["applicant"])
    track = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "HIE-CM"],
    )
    page = client.get(track, {"milestone": "m1"}).content.decode()
    assert f"Reason:</span> {DOCUMENTS}" in page
    assert "The FT certificate covers M1 only." in page


def test_a_listed_reason_needs_no_note_but_other_does(environment):
    item = submit(environment)
    reviewer = environment["reviewer"]
    with pytest.raises(ValidationError, match="Choose a reason"):
        services.decide(item, reviewer, action="reject", note="Look again.")
    with pytest.raises(ValidationError, match="Choose a reason from the list"):
        services.decide(item, reviewer, action="reject", reason="Made up")
    with pytest.raises(ValidationError, match="Write the reason"):
        services.decide(item, reviewer, action="reject", reason=OTHER_REASON)

    item = services.decide(item, reviewer, action="reject", reason=DOCUMENTS)

    assert (item.decision_reason, item.decision_note) == (DOCUMENTS, "")


def test_ten_characters_are_asked_of_the_notes_that_carry_the_reason(environment):
    """The floor guards a required note; an aside beside a listed reason is free."""
    item = submit(environment)
    reviewer = environment["reviewer"]
    with pytest.raises(ValidationError, match="at least 10 characters"):
        services.decide(
            item,
            reviewer,
            action="reject",
            reason=OTHER_REASON,
            note="Too short",
        )

    item = services.decide(
        item,
        reviewer,
        action="reject",
        reason=DOCUMENTS,
        note="Redo it.",
    )

    assert (item.decision_reason, item.decision_note) == (DOCUMENTS, "Redo it.")


def test_the_reason_is_cleared_when_the_integrator_resubmits(environment):
    item = submit(environment)
    services.decide(
        item,
        environment["reviewer"],
        action="reject",
        reason=OTHER_REASON,
        note="The demo recording is unplayable.",
    )

    item = submit(environment)

    assert item.decision_reason == ""


def product_page(environment):
    return reverse(
        "experiences:product-detail",
        args=[environment["workspace"].reference],
    )


def test_the_product_page_rejects_one_request_with_a_reason(environment, client):
    item = submit(environment)
    client.force_login(environment["reviewer"])
    page = client.get(product_page(environment))
    assert f'id="decision-reason-{item.pk}"'.encode() in page.content

    response = client.post(
        product_page(environment),
        {
            "intent": "decision",
            "review_id": item.pk,
            "revision": item.selected_submission_id,
            "action": "reject",
            "reason": DOCUMENTS,
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    item.refresh_from_db()
    assert (item.status, item.decision_reason) == ("rejected", DOCUMENTS)


def test_rejecting_a_batch_takes_the_note_alone(environment, client):
    """A batch can mix forms with different lists, so it has no reason to choose."""
    item = submit(environment)
    client.force_login(environment["reviewer"])

    response = client.post(
        product_page(environment),
        {
            "intent": "bulk_decision",
            "action": "reject",
            "reviews": [f"{item.pk}:{item.selected_submission_id}"],
            "note": "Every certificate needs the agency's seal.",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    item.refresh_from_db()
    assert (item.status, item.decision_reason) == ("rejected", "")
