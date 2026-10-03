from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import review_section
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.templatetags.experience_ui import open_queries
from ohc_experience.experiences.templatetags.experience_ui import snapshot_rows
from ohc_experience.experiences.tests.example_program import SupplierQuality
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def last_flash(response):
    """The newest message; the test client follows no redirect, so older ones
    are still queued."""
    return [str(message) for message in get_messages(response.wsgi_request)][-1]


def product_page(item):
    return reverse("experiences:product-detail", args=[item.product.reference])


def decision(item, action, note="", **fields):
    """A request's own Decision form on the product page."""
    return {
        "intent": "decision",
        "review_id": item.pk,
        "revision": item.selected_submission_id,
        "action": action,
        "note": note,
        **fields,
    }


def section(response, item):
    """The context a request's card on the product page renders from."""
    return next(
        row for row in response.context["review_sections"] if row["item"] == item
    )


@pytest.fixture
def review_item(monkeypatch, settings, owner_membership):
    for attribute in ("_definitions", "_forms", "_programs"):
        monkeypatch.setattr(registry, attribute, dict(getattr(registry, attribute)))
    registry.register_program(SupplierQuality)
    settings.EXPERIENCE_PORTAL = SupplierQuality.key
    product, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump",
            "summary": "Industrial equipment",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert product, form.errors
    item = product.milestones.get(key="inspection").application.review_item
    item, form, saved = workflows.save_review_form(
        item,
        owner_membership.user,
        data={"report_reference": "Q-PORT-1", "score": 95},
        submit=True,
    )
    assert saved, form.errors
    return item


@pytest.mark.parametrize("route", ["assess-dashboard", "queue", "pending-queries"])
def test_reviewer_surfaces_render_with_another_program(review_item, client, route):
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(reverse(f"experiences:{route}"))
    assert response.status_code == HTTPStatus.OK
    assert b"Supplier Quality Portal" in response.content
    assert b"ABDM" not in response.content
    assert b"HIE-CM" not in response.content


def test_production_access_is_absent_for_a_program_that_does_not_record_it(
    review_item,
    client,
):
    client.force_login(ReviewerFactory(is_nha_team=True))
    assert (
        b'id="nav-production"' not in client.get(reverse("experiences:queue")).content
    )
    for route in ["production-list", "production-export"]:
        response = client.get(reverse(f"experiences:{route}"))
        assert response.status_code == HTTPStatus.NOT_FOUND


def test_queue_filters_still_work_when_requested_through_htmx(review_item, client):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    response = client.get(
        reverse("experiences:queue"),
        {
            "assignee": "me",
            "item": "Quality",
            "q": "Water pump",
            "status": "in_review",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == HTTPStatus.OK
    assert [entry.product for entry in response.context["page"]] == [
        review_item.product,
    ]
    assert response.context["page"][0].matching_reviews == [review_item]
    assert (
        reverse(
            "experiences:product-detail",
            args=[review_item.product.reference],
        ).encode()
        in response.content
    )
    assert b'aria-label="Queue pagination"' not in response.content
    response = client.get(reverse("experiences:queue"), {"q": "No such equipment"})
    assert response.context["page"].paginator.count == 0
    assert b"No reviews match these filters." in response.content


def test_queue_searches_by_product_reference_and_preserves_it_in_navigation(
    review_item,
    owner_membership,
    client,
):
    other_product, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump accessory",
            "summary": "A separate product with a similar name",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert other_product, form.errors
    other_item = other_product.milestones.get(
        key="inspection",
    ).application.review_item
    other_item, form, saved = workflows.save_review_form(
        other_item,
        owner_membership.user,
        data={"report_reference": "Q-PORT-2", "score": 90},
        submit=True,
    )
    assert saved, form.errors

    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)
    reference = review_item.product.reference
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "ready", "q": reference},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.context["filters"]["q"] == reference
    assert {item.product_id for item in response.context["page"]} == {
        review_item.product_id,
    }
    assert other_item.product_id not in {
        entry.product_id for entry in response.context["page"]
    }
    assert f'value="{reference}"'.encode() in response.content
    assert (
        f"?scope=decided&amp;status=&amp;item=&amp;assignee=&amp;q={reference}".encode()
        in response.content
    )
    assert f"q={reference}".encode() in response.context["filter_query"].encode()
    assert b'href="?scope=ready"' in response.content


def test_staff_product_page_links_open_requests_to_their_cards(
    review_item,
    owner_membership,
    client,
):
    product = review_item.product
    url = reverse("experiences:product-detail", args=[product.reference])
    client.force_login(ReviewerFactory(is_nha_team=True))
    assert client.get(product.get_absolute_url()).url == url
    response = client.get(url)

    assert response.status_code == HTTPStatus.OK
    assert review_item in response.context["pending"]
    assert review_item.pk in response.context["decidable"]
    assert f'href="#review-{review_item.pk}"'.encode() in response.content
    assert b"Supplier Quality Portal" in response.content
    assert b'id="product-switcher' not in response.content
    assert (
        f"{reverse('experiences:queue')}?scope=ready&amp;q={product.reference}".encode()
        in response.content
    )

    client.force_login(owner_membership.user)
    response = client.get(product.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Needs a decision" not in response.content
    assert client.get(url).status_code == HTTPStatus.FORBIDDEN
    assert client.get(reverse("experiences:queue")).status_code == HTTPStatus.FORBIDDEN


def test_review_decisions_follow_grants_and_assignment_only_labels(review_item, client):
    url = product_page(review_item)
    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)
    card = review_section(client.get(url).content.decode(), review_item)
    assert "data-decision-form" in card
    assert "field=form#review-" in card
    # Each submitted field picks itself for a query, falling back to a link.
    assert 'data-query-field="score"' in card
    assert "field=score#review-" in card
    # Approving a request includes choosing its reviewer.
    assert 'name="assignee"' in card

    read_only = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=read_only,
        program=SupplierQuality.key,
        area=AccessGrant.Area.REVIEW,
        category="*",
    )
    client.force_login(read_only)
    card = review_section(client.get(url).content.decode(), review_item)
    assert "data-decision-form" not in card
    assert "data-query-field" not in card
    assert "You have read-only access to this request." in card
    assert 'name="assignee"' not in card

    client.force_login(UserFactory(is_superuser=True))
    card = review_section(client.get(url).content.decode(), review_item)
    assert 'name="assignee"' in card
    assert "Reassign" not in card
    response = client.post(
        url,
        {"intent": "assign", "review_id": review_item.pk, "assignee": reviewer.pk},
    )
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.assignee == reviewer
    assert "Reassign" in review_section(client.get(url).content.decode(), review_item)

    # Someone other than the assignee can still record the decision.
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.post(
        url,
        decision(review_item, "approve", "Evidence accepted."),
    )
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.status == "approved"
    assert review_item.assignee == reviewer


def test_a_required_note_is_refused_until_it_runs_to_ten_characters(
    review_item,
    owner_membership,
    client,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)

    response = client.post(
        product_page(review_item),
        decision(review_item, "query", question_score="Too short"),
    )

    assert response.status_code == HTTPStatus.OK
    assert b"at least 10 characters for the query about Score" in response.content
    assert not review_item.queries.exists()
    # The question comes back in its box, to be finished rather than retyped.
    assert section(response, review_item)["query_drafts"] == {"score": "Too short"}


def test_query_validation_reply_resolution_and_approval_through_portal(  # noqa: PLR0915
    review_item,
    owner_membership,
    client,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    url = product_page(review_item)
    response = client.post(url, decision(review_item, "query"))
    assert response.status_code == HTTPStatus.OK
    assert section(response, review_item)["decision_action"] == "query"
    assert b"Write a query before sending." in response.content

    response = client.post(
        url,
        decision(review_item, "query", question_score="Confirm this score."),
    )
    assert response.status_code == HTTPStatus.FOUND
    assert last_flash(response) == f"Query raised on {review_item.title}."
    query = review_item.queries.get()
    response = client.get(url)
    assert section(response, review_item)["unresolved_query_count"] == 1
    assert b'data-approval-blocked="true"' in response.content
    card = " ".join(review_section(response.content.decode(), review_item).split())
    assert "Confirm this score." in card
    assert "Resolve 1 outstanding query before approval." in card
    assert "1 awaiting reply" in card

    client.force_login(owner_membership.user)
    response = client.get(reverse("experiences:pending-queries"))
    assert b"Action required from applicant" in response.content
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"body": "Confirmed against the inspection report."},
    )
    assert response.status_code == HTTPStatus.FOUND
    assert last_flash(response) == "Reply sent."
    client.force_login(reviewer)
    response = client.get(url)
    assert section(response, review_item)["unresolved_query_count"] == 1
    card = " ".join(review_section(response.content.decode(), review_item).split())
    assert "Mark resolved" in card
    assert "1 to review" in card
    assert "awaiting reply" not in card
    pending = client.get(reverse("experiences:pending-queries"))
    assert b"Replies received" in pending.content
    assert b"Awaiting reply" not in pending.content
    assert f'href="{url}#queries-{review_item.pk}"'.encode() in pending.content
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"intent": "resolve"},
    )
    assert response.status_code == HTTPStatus.FOUND
    assert last_flash(response) == "Query resolved."
    response = client.post(url, decision(review_item, "approve", "Evidence verified."))
    assert response.status_code == HTTPStatus.FOUND
    assert last_flash(response) == f"{review_item.title} approved."
    review_item.refresh_from_db()
    assert review_item.status == "approved"
    response = client.get(url)
    card = review_section(response.content.decode(), review_item)
    assert "Evidence verified." in card
    assert "data-decision-form" not in card
    snapshot_url = reverse(
        "experiences:submission",
        args=[review_item.pk, review_item.selected_submission_id],
    )
    assert snapshot_url.encode() in response.content
    response = client.get(snapshot_url)
    assert response.status_code == HTTPStatus.OK
    assert b"Q-PORT-1" in response.content
    assert b"Water pump" in response.content
    assert review_item.reference.encode() in response.content


def test_queries_open_in_full_only_for_whoever_can_act_on_them(
    review_item,
    client,
    owner_membership,
):
    integrator = owner_membership.user
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    workflows.decide(
        review_item,
        reviewer,
        action="query",
        field_key="score",
        note="Asked of the first submission.",
    )
    # Resubmitting instead of replying leaves that query on the earlier submission.
    workflows.withdraw(review_item, integrator)
    review_item.refresh_from_db()
    item, form, saved = workflows.save_review_form(
        review_item,
        integrator,
        data={"report_reference": "Q-PORT-2", "score": 96},
        submit=True,
    )
    assert saved, form.errors
    item.refresh_from_db()
    for key, note in [
        ("report_reference", "Which inspection is this?"),
        ("score", "Confirm this score."),
        ("form", "Attach the release note."),
    ]:
        workflows.decide(item, reviewer, action="query", field_key=key, note=note)
        item.refresh_from_db()
    asked = {
        query.field_key: query
        for query in item.queries.filter(submission=item.selected_submission)
    }
    workflows.reply_query(asked["score"], integrator, "Confirmed against the report.")
    workflows.reply_query(asked["form"], integrator, "The release note is attached.")
    workflows.resolve_query(asked["form"], reviewer)

    def queries_card(user, url, anchor='id="queries"'):
        client.force_login(user)
        page = client.get(url).content.decode()
        return page[page.index(anchor) :]

    folded = '<details class="group/q'
    settled = '<details class="group/settled'

    # The reviewer can resolve the reply, so it opens in full, although it was
    # asked after the question still waiting on the integrator, which folds.
    card = queries_card(reviewer, product_page(item), f'id="queries-{item.pk}"')
    assert "1 to review · 1 awaiting reply" in " ".join(card.split())
    assert (
        card.index("Mark resolved")
        < card.index("Confirm this score.")
        < card.index(folded)
        < card.index("Which inspection is this?")
        < card.index(settled)
    )
    # Resolved queries and those on an earlier submission settle together.
    assert "Show 2 settled" in card
    assert card.index(settled) < card.index("Attach the release note.")
    assert card.index(settled) < card.index("Asked of the first submission.")

    # The integrator is the one who can reply, so the open question opens in
    # full with the reply form, and their answered one folds.
    track = reverse(
        "experiences:track",
        args=[item.product.reference, "Quality"],
    )
    card = queries_card(integrator, f"{track}?milestone=inspection")
    assert "1 awaiting your reply · 1 with reviewer" in " ".join(card.split())
    assert (
        card.index("Which inspection is this?")
        < card.index("Send reply</button>")
        < card.index(folded)
        < card.index("With reviewer")
        < card.index("Confirm this score.")
    )
    assert "Mark resolved" not in card[: card.index(settled)]


def test_the_product_page_names_who_submitted_each_request(
    review_item,
    owner_membership,
    client,
):
    owner_membership.user.phone_number = "+919876543210"
    owner_membership.user.save(update_fields=["phone_number"])
    # Only an organisation's own verification is judged by its website.
    organisation = review_item.organisation
    organisation.website = "https://sunrise-health.example"
    organisation.entity_type = "private_company"
    organisation.save()
    client.force_login(ReviewerFactory(is_nha_team=True))

    html = client.get(product_page(review_item)).content.decode()
    card = review_section(html, review_item)
    row = card.split("Submitted by</dt>", 1)[1].split("</dd>", 1)[0]

    assert "Meera Krishnan" in row
    assert 'href="mailto:meera@sunrise.in"' in row
    assert "+919876543210" in row
    assert "Not on the website's domain" not in row


def test_the_product_page_asks_within_that_requests_decision(review_item, client):
    client.force_login(ReviewerFactory(is_nha_team=True))

    html = client.get(
        reverse(
            "experiences:product-detail",
            args=[review_item.product.reference],
        ),
    ).content.decode()

    form = f"decision-form-{review_item.pk}"
    assert f'id="{form}"' in html
    assert f'data-query-for="{form}"' in html
    assert 'data-query-field="score"' in html
    assert f'data-query-form="{form}"' in html
    # Each request's questions belong to its own form.
    assert f'name="question_score"\n          form="{form}"' in html
    assert "Query the whole form</a>" in html


def test_ask_opens_a_closed_question_box_under_its_field(review_item, client):
    client.force_login(ReviewerFactory(is_nha_team=True))
    url = product_page(review_item)
    form = f"decision-form-{review_item.pk}"

    def score_box(html):
        box = html[html.index(f'id="draft-{form}-score"') :]
        return box[: box.index("</dd>")]

    html = client.get(url).content.decode()
    box = score_box(html)
    # Closed: hidden, and its question neither sends nor blocks a send.
    assert "hidden" in box.split(">", 1)[0]
    assert "disabled" in box
    assert 'name="question_score"' in box
    assert f'form="{form}"' in box
    assert 'Query<span class="sr-only"> Score</span></a>' in html
    assert "Against" not in html

    # Without scripts, Ask is a link that reloads with the card and box open.
    link = f"{url}?review={review_item.pk}&amp;action=query&amp;field=score"
    assert f'href="{link}#review-{review_item.pk}"' in html
    html = client.get(link.replace("&amp;", "&")).content.decode()
    box = score_box(html)
    assert "hidden" not in box.split(">", 1)[0]
    assert "disabled" not in box
    panel = html[html.index(f'id="review-{review_item.pk}"') :]
    assert panel[: panel.index(">")].split()[-1] == "open"


def test_several_questions_go_to_the_integrator_in_one_send(review_item, client):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    mail.outbox.clear()

    response = client.post(
        product_page(review_item),
        decision(
            review_item,
            "query",
            question_form="Attach the signed release note.",
            question_score="Confirm this score against the report.",
        ),
    )

    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.status == "query_raised"
    assert [
        (query.field_key, query.question) for query in review_item.queries.all()
    ] == [
        ("form", "Attach the signed release note."),
        ("score", "Confirm this score against the report."),
    ]
    assert sorted(
        event.detail["field"]
        for event in review_item.history.filter(action="Query raised")
    ) == ["form", "score"]
    # One email names every question under the field it asks about.
    [email] = mail.outbox
    assert "Whole form:\nAttach the signed release note." in email.body
    assert "Score:\nConfirm this score against the report." in email.body


def test_a_batch_of_questions_is_sent_whole_or_not_at_all(review_item):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)

    with pytest.raises(ValidationError, match="query about Score"):
        workflows.raise_queries(
            review_item,
            reviewer,
            [("form", "Attach the signed release note."), ("score", "Too short")],
        )
    with pytest.raises(ValidationError, match="Choose a field"):
        workflows.raise_queries(
            review_item,
            reviewer,
            [("retired", "Which one is it?")],
        )
    with pytest.raises(ValidationError, match="Write a query"):
        workflows.raise_queries(review_item, reviewer, [])
    assert not review_item.queries.exists()


def test_each_queried_field_leads_the_integrator_to_its_query(
    review_item,
    client,
    owner_membership,
):
    integrator = owner_membership.user
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    for key, note in [
        ("score", "Confirm this score against the report."),
        ("form", "Attach the signed release note."),
        ("score", "Say which page of the report carries it."),
    ]:
        workflows.decide(
            review_item,
            reviewer,
            action="query",
            field_key=key,
            note=note,
        )
        review_item.refresh_from_db()
    first_score, form, second_score = review_item.queries.all()
    track = reverse(
        "experiences:track",
        args=[review_item.product.reference, "Quality"],
    )
    client.force_login(integrator)

    def page():
        html = client.get(f"{track}?milestone=inspection").content.decode()
        alert = html[html.index("Your response is needed") :]
        alert = alert[: alert.index("Withdraw request")]
        submitted = html[
            html.index(">Submitted form</h2>") : html.index('id="queries"')
        ]
        return " ".join(alert.split()), submitted

    alert, submitted = page()
    # The alert names each field queried, once.
    assert (
        'The reviewer raised queries on <span class="font-semibold">Score</span> and '
        '<span class="font-semibold">the whole form</span>.'
    ) in alert
    # It leads to the first of them, as Continue leads to the first gap.
    assert (
        f'href="#query-{first_score.pk}" data-query-link>Reply to the queries</a>'
    ) in alert
    # The form marks what was queried and links to it: the whole form above
    # the fields, a field in its own label. The questions stay in the queries
    # card, so a long one cannot stretch the grid.
    for query in (first_score, form, second_score):
        assert query.question not in submitted
    assert (
        submitted.index(f'href="#query-{form.pk}"')
        < submitted.index(">Report Reference")
        < submitted.index(">Score")
        < submitted.index(f'href="#query-{first_score.pk}"')
    )

    # Answering both of a field's queries clears its mark.
    workflows.reply_query(first_score, integrator, "It matches the report.")
    workflows.reply_query(second_score, integrator, "Page three.")
    alert, submitted = page()
    assert "Score" not in alert
    assert f'href="#query-{form.pk}" data-query-link>Reply to the query</a>' in alert
    assert f'href="#query-{first_score.pk}"' not in submitted
    assert f'href="#query-{form.pk}"' in submitted


def test_a_withdrawn_request_marks_no_field_as_queried(review_item, owner_membership):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    workflows.decide(
        review_item,
        reviewer,
        action="query",
        field_key="score",
        note="Confirm this score.",
    )
    review_item.refresh_from_db()

    def rows():
        return {
            row["key"]: row
            for row in snapshot_rows(review_item.selected_submission, review_item)
        }

    assert rows()["score"]["query_open"]
    assert [query.question for query in rows()["score"]["queries"]] == [
        "Confirm this score.",
    ]

    # The queries card files it as settled, and the form agrees.
    workflows.withdraw(review_item, owner_membership.user)
    review_item.refresh_from_db()
    assert not rows()["score"]["query_open"]
    assert open_queries(review_item) == []


def test_client_response_alert_is_not_shown_to_reviewer(review_item):
    review_item.status = "query_raised"

    reviewer_html = render_to_string(
        "experiences/partials/review_status.html",
        {"item": review_item, "reviewer": True},
    )
    client_html = render_to_string(
        "experiences/partials/review_status.html",
        {"item": review_item, "reviewer": False},
    )

    assert "Your response is needed" not in reviewer_html
    assert 'role="status"' not in reviewer_html
    assert "Your response is needed" in client_html


@pytest.mark.parametrize(
    ("scope", "incompatible_status", "expected_statuses"),
    [
        ("ready", "approved", set()),
        ("ready", "query_raised", set()),
        # A saved link may still carry the status that is gone.
        ("decided", "new", {"waiting", "query_raised", "rejected", "approved"}),
        (
            "all",
            "draft",
            {"in_review", "waiting", "query_raised", "rejected", "approved"},
        ),
    ],
)
def test_queue_scope_removes_conflicting_status_without_losing_other_filters(
    review_item,
    client,
    scope,
    incompatible_status,
    expected_statuses,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    if scope == "decided":
        response = client.post(
            product_page(review_item),
            decision(review_item, "approve", "Evidence accepted."),
        )
        assert response.status_code == HTTPStatus.FOUND

    response = client.get(
        reverse("experiences:queue"),
        {
            "scope": scope,
            "status": incompatible_status,
            "assignee": "me",
            "item": "Quality",
            "q": "Water pump",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == HTTPStatus.OK
    assert [entry.product for entry in response.context["page"]] == [
        review_item.product,
    ]
    assert response.context["page"][0].matching_reviews == [review_item]
    assert "status" not in response.context["filters"]
    assert "status=" not in response.context["filter_query"]
    assert set(dict(response.context["statuses"])) == expected_statuses
    assert response.context["filters"]["assignee"] == "me"
    assert response.context["filters"]["item"] == "Quality"
    assert response.context["filters"]["q"] == "Water pump"
    assert f'href="?scope={scope}"'.encode() in response.content


def test_empty_personal_queue_keeps_its_scope_when_clearing_filters(
    review_item,
    client,
):
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "ready", "assignee": "me", "q": "not found"},
    )
    assert b"No reviews match these filters." in response.content
    assert b'href="?scope=ready"' in response.content
    response = client.get(reverse("experiences:queue"), {"scope": "decided"})
    assert b"No requests in this view" in response.content
    assert b"View all requests" in response.content
    assert b"Clear filters" not in response.content


VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    },
)


class LinkSwaps(HTMLParser):
    """How htmx swaps in the response to each link on a page, by link text.

    A link htmx handles maps to the element its request replaces and the part of
    the response put in its place, inherited from ancestors as htmx inherits
    them. A link htmx leaves alone maps to None.
    """

    def __init__(self, html):
        super().__init__()
        self.open_elements = []
        self.link = None
        self.swaps = {}
        self.feed(html)

    def inherited(self, name):
        return next(
            (attrs[name] for _, attrs in reversed(self.open_elements) if name in attrs),
            None,
        )

    def swap(self, attrs):
        # Without hx-target, htmx swaps an hx-get link's response into the link
        # itself, and a boosted link's into the body.
        if "hx-get" in attrs:
            target = "this"
        elif self.inherited("hx-boost") == "true":
            target = "body"
        else:
            return None
        return self.inherited("hx-target") or target, self.inherited("hx-select")

    def handle_starttag(self, tag, attrs):
        if tag in VOID_ELEMENTS:
            return
        attrs = dict(attrs)
        self.open_elements.append((tag, attrs))
        if tag == "a":
            self.link = ([], self.swap(attrs))

    def handle_data(self, data):
        if self.link:
            self.link[0].append(data)

    def handle_endtag(self, tag):
        if tag not in (name for name, _ in self.open_elements):
            return
        while self.open_elements.pop()[0] != tag:
            pass
        if tag == "a":
            words, swap = self.link
            self.swaps.setdefault(" ".join("".join(words).split()), []).append(swap)
            self.link = None


def test_empty_queue_links_replace_the_queue_instead_of_nesting_the_page(
    review_item,
    client,
):
    client.force_login(ReviewerFactory(is_nha_team=True))
    queue = ("#review-queue", "#review-queue")
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "ready", "q": "not found"},
    )
    assert b"No reviews match these filters." in response.content
    swaps = LinkSwaps(response.content.decode()).swaps
    assert swaps["Clear filters"] == [queue, queue]
    response = client.get(reverse("experiences:queue"), {"scope": "decided"})
    assert b"No requests in this view" in response.content
    assert LinkSwaps(response.content.decode()).swaps["View all requests"] == [queue]
