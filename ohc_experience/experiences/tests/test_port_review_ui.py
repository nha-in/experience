from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.tests.example_program import SupplierQuality
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def review_item(monkeypatch, settings, owner_membership):
    for attribute in ("_definitions", "_forms", "_programs"):
        monkeypatch.setattr(registry, attribute, dict(getattr(registry, attribute)))
    registry.register_program(SupplierQuality)
    settings.EXPERIENCE_PORTAL = SupplierQuality.key
    workspace, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump",
            "summary": "Industrial equipment",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert workspace, form.errors
    item = workspace.product.milestones.get(key="inspection").application.review_item
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
            args=[review_item.product.workspace.reference],
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
    other_workspace, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump accessory",
            "summary": "A separate product with a similar name",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert other_workspace, form.errors
    other_item = other_workspace.product.milestones.get(
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
    reference = review_item.product.workspace.reference
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


def test_staff_product_page_links_open_requests_to_their_reviews(
    review_item,
    owner_membership,
    client,
):
    workspace = review_item.product.workspace
    url = reverse("experiences:product-detail", args=[workspace.reference])
    client.force_login(ReviewerFactory(is_nha_team=True))
    assert client.get(workspace.get_absolute_url()).url == url
    response = client.get(url)

    assert response.status_code == HTTPStatus.OK
    assert review_item in response.context["pending"]
    assert review_item.pk in response.context["decidable"]
    assert review_item.get_absolute_url().encode() in response.content
    assert b"Supplier Quality Portal" in response.content
    assert b'id="product-switcher' not in response.content
    assert (
        f"{reverse('experiences:queue')}?scope=ready&amp;q={workspace.reference}".encode()
        in response.content
    )

    client.force_login(owner_membership.user)
    response = client.get(workspace.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Needs a decision" not in response.content
    assert client.get(url).status_code == HTTPStatus.FORBIDDEN
    assert client.get(reverse("experiences:queue")).status_code == HTTPStatus.FORBIDDEN


def test_review_decisions_follow_grants_and_assignment_only_labels(review_item, client):
    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)
    response = client.get(review_item.get_absolute_url())
    assert b"data-decision-form" in response.content
    assert b"field=form#decision" in response.content
    assert b"field=score#decision" not in response.content
    assert b'name="assignee"' not in response.content

    read_only = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=read_only,
        program=SupplierQuality.key,
        area=AccessGrant.Area.REVIEW,
        category="*",
    )
    client.force_login(read_only)
    response = client.get(review_item.get_absolute_url())
    assert b"data-decision-form" not in response.content
    assert b"does not include queries or decisions" in response.content

    client.force_login(UserFactory(is_superuser=True))
    response = client.get(review_item.get_absolute_url())
    assert b'name="assignee"' in response.content
    assert b"Reassign" not in response.content
    response = client.post(
        review_item.get_absolute_url(),
        {"intent": "assign", "assignee": reviewer.pk},
    )
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.assignee == reviewer
    assert b"Reassign" in client.get(review_item.get_absolute_url()).content

    # Someone other than the assignee can still record the decision.
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.post(
        review_item.get_absolute_url(),
        {"action": "approve", "note": "Evidence accepted."},
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
        review_item.get_absolute_url(),
        {"action": "query", "field_key": "score", "note": "Too short"},
    )

    assert response.status_code == HTTPStatus.OK
    assert b"at least 10 characters" in response.content
    assert not review_item.queries.exists()


def test_query_validation_reply_resolution_and_approval_through_portal(
    review_item,
    owner_membership,
    client,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    url = review_item.get_absolute_url()
    response = client.post(
        url,
        {"action": "query", "field_key": "score", "note": ""},
    )
    assert response.status_code == HTTPStatus.OK
    assert response.context["decision_action"] == "query"
    assert response.context["query_field"] == "score"
    assert b"Enter a reason or question" in response.content

    response = client.post(
        url,
        {"action": "query", "field_key": "score", "note": "Confirm this score."},
    )
    assert response.status_code == HTTPStatus.FOUND
    query = review_item.queries.get()
    response = client.get(url)
    assert response.context["unresolved_query_count"] == 1
    assert response.context["awaiting_reply_count"] == 1
    assert b'data-approval-blocked="true"' in response.content
    assert b"Confirm this score." in response.content
    assert b"waiting for the integrator" in response.content

    client.force_login(owner_membership.user)
    response = client.get(reverse("experiences:pending-queries"))
    assert b"Reply needed" in response.content
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"body": "Confirmed against the inspection report."},
    )
    assert response.status_code == HTTPStatus.FOUND
    client.force_login(reviewer)
    response = client.get(url)
    assert response.context["unresolved_query_count"] == 1
    assert response.context["awaiting_reply_count"] == 0
    assert b"Mark resolved" in response.content
    assert b"Review replies" in response.content
    assert b"ready for your review" in response.content
    pending = client.get(reverse("experiences:pending-queries"))
    assert b"Replies received" in pending.content
    assert b"Awaiting reply" not in pending.content
    assert f"{url}#queries".encode() in pending.content
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"intent": "resolve"},
    )
    assert response.status_code == HTTPStatus.FOUND
    response = client.post(url, {"action": "approve", "note": "Evidence verified."})
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.status == "approved"
    response = client.get(url)
    assert b"Evidence verified." in response.content
    assert b"data-decision-form" not in response.content
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

    def queries_card(user, url):
        client.force_login(user)
        page = client.get(url).content.decode()
        return page[page.index('id="queries"') :]

    folded = '<details class="group/q'
    settled = '<details class="group/settled'

    # The reviewer can resolve the reply, so it opens in full, although it was
    # asked after the question still waiting on the integrator, which folds.
    card = queries_card(reviewer, item.get_absolute_url())
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
        args=[item.product.workspace.reference, "Quality"],
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


def test_the_review_page_names_who_submitted_and_how_to_reach_them(
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

    html = client.get(review_item.get_absolute_url()).content.decode()
    row = html.split("Submitted by</dt>", 1)[1].split("</dd>", 1)[0]

    assert "Meera Krishnan" in row
    assert 'href="mailto:meera@sunrise.in"' in row
    assert "+919876543210" in row
    assert "Not on the website's domain" not in row


def test_the_product_page_names_who_submitted_each_request(review_item, client):
    client.force_login(ReviewerFactory(is_nha_team=True))

    html = client.get(
        reverse(
            "experiences:product-detail",
            args=[review_item.product.workspace.reference],
        ),
    ).content.decode()
    row = html.split("Submitted by</dt>", 1)[1].split("</dd>", 1)[0]

    assert "Meera Krishnan" in row
    assert 'href="mailto:meera@sunrise.in"' in row


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
        ("ready", "approved", {"new", "in_review", "query_raised"}),
        ("decided", "in_review", {"approved", "rejected"}),
        (
            "all",
            "draft",
            {"new", "in_review", "query_raised", "approved", "rejected"},
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
            review_item.get_absolute_url(),
            {"action": "approve", "note": "Evidence accepted."},
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
