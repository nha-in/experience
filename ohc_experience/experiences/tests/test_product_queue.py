# ruff: noqa: F811, PLR2004
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.abdm.tests.test_workflow import submit_claims
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.tests.test_port_review_ui import (
    review_item,  # noqa: F401
)
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def submit_release(inspection, owner):
    release = inspection.product.milestones.get(key="release").application.review_item
    release, form, saved = workflows.save_review_form(
        release,
        owner,
        data={"report_reference": "Q-RELEASE", "score": 96},
        submit=True,
    )
    assert saved, form.errors
    return release


def test_a_rejected_product_takes_its_requests_out_of_the_queue(
    environment,
    client,
):
    """Nobody decides work on a product whose registration was turned down."""
    m1 = submit(environment, "m1")
    client.force_login(environment["reviewer"])
    assert m1 in [
        review
        for entry in client.get(reverse("experiences:queue")).context["page"]
        for review in entry.reviews
    ]

    registration = environment["product"].registration
    registration.status = ReviewItem.Status.REJECTED
    registration.save(update_fields=["status"])

    response = client.get(reverse("experiences:queue"))

    assert [
        review for entry in response.context["page"] for review in entry.reviews
    ] == []


def test_product_queue_combines_submitted_milestones_and_hides_unsubmitted(
    environment,
    client,
):
    m1 = submit(environment, "m1")
    m2 = submit(environment, "m2")
    m3 = milestone(environment, "m3")
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:queue"))

    page = response.context["page"]
    assert page.paginator.count == 1
    entry = page[0]
    assert {m1, m2}.issubset(entry.reviews)
    assert m3 not in entry.reviews
    codes = [
        item.queue_milestone.code for item in entry.reviews if item.queue_milestone
    ]
    assert codes == ["M1", "M2"]
    # Only M1 can be decided now; M2 waits on it and rides along on its row.
    assert entry.matching_reviews == [m1]
    assert entry.url == reverse(
        "experiences:product-detail",
        args=[environment["product"].reference],
    )
    assert entry.url.encode() in response.content
    assert b"Not submitted" not in response.content
    assert m3.get_absolute_url().encode() not in response.content
    # M2 waits on M1, and still shows beside it; the product counts once.
    assert response.context["stage_counts"]["ready"] == 1
    assert "waiting" not in response.context["stage_counts"]


def test_queue_chips_put_what_can_be_decided_first_and_the_rest_below(
    environment,
    client,
):
    submit(environment, "m1")
    submit(environment, "m2")
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")

    def rows(response):
        html = response.content.decode()
        pending = html[html.index("data-queue-pending") : html.index("data-queue-done")]
        done = html[html.index("data-queue-done") :].split("</div>", 1)[0]
        return " ".join(pending.split()), " ".join(done.split())

    # M2 waits on M1, so only All shows it beside M1; Pending leaves it out.
    for scope, done in (("all", ["approved", "blocked"]), ("ready", ["approved"])):
        response = client.get(url, {"scope": scope})
        entry = response.context["page"][0]
        assert [item.queue_state for item in entry.pending_reviews] == ["in_review"]
        assert [item.queue_state for item in entry.done_reviews] == done
        pending_row, done_row = rows(response)
        assert ("M2 waiting on M1" in done_row) == (scope == "all")
        assert "ui-queue-chip--blocked" not in pending_row
        assert "ui-queue-chip--approved" not in pending_row
        assert "ui-queue-chip--approved" in done_row
        assert "of 3 approved" not in done_row


def test_product_with_an_open_request_is_pending_only_and_done_once_decided(
    environment,
    client,
):
    submit(environment, "m1")
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")

    response = client.get(url, {"scope": "decided"})
    assert not response.context["page"]
    assert response.context["stage_counts"] == {"ready": 1, "decided": 0, "all": 1}

    ReviewItem.objects.filter(status__in=workflows.PENDING_STATUSES).update(
        status=ReviewItem.Status.APPROVED,
    )
    response = client.get(url, {"scope": "decided"})
    assert response.context["page"][0].product == environment["product"]
    assert response.context["stage_counts"] == {"ready": 0, "decided": 1, "all": 1}


def test_filters_select_products_and_narrow_their_chips_to_matching_requests(
    client,
    review_item,
    owner_membership,
):
    release = submit_release(review_item, owner_membership.user)
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(release, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    # Under review, but held by INS: nothing of this reviewer's can be decided
    # now, so it is done for them, and its chip and the filter say it waits.
    filters = {
        "scope": "decided",
        "item": "Quality",
        "status": "waiting",
        "assignee": "me",
        "q": release.application.reference,
    }
    response = client.get(
        reverse("experiences:queue"),
        filters,
        HTTP_HX_REQUEST="true",
    )

    assert response.context["page"].paginator.count == 1
    entry = response.context["page"][0]
    assert entry.reviews == [release, review_item]
    assert entry.matching_reviews == [release]
    assert entry.assignees == [reviewer]
    assert response.context["stage_counts"] == {
        "ready": 0,
        "decided": 1,
        "all": 1,
    }
    assert b"Waiting on INS" in response.content
    compact = " ".join(response.content.decode().split())
    assert 'title="REL Release · Under review · waiting on INS"' in compact
    assert 'title="INS Inspection' not in compact
    assert "REL waiting on INS" in compact
    assert "data-queue-pending" not in compact
    # Its own status no longer finds it: no chip on the row would say so.
    filters.update(scope="all", status="in_review")
    assert not client.get(reverse("experiences:queue"), filters).context["page"]


def test_a_query_moves_a_request_to_done_until_the_integrator_answers(
    environment,
    client,
):
    """Pending is what a reviewer can decide now, and Done is everything else."""
    m1 = submit(environment, "m1")
    reviewer = environment["reviewer"]
    client.force_login(reviewer)
    url = reverse("experiences:queue")

    def rows(scope):
        response = client.get(url, {"scope": scope})
        return [
            (
                [item.queue_state for item in entry.pending_reviews],
                [item.queue_state for item in entry.done_reviews],
            )
            for entry in response.context["page"]
        ]

    assert client.get(url).context["statuses"] == []
    assert b'id="queue-status"' not in client.get(url).content
    assert client.get(url, {"scope": "decided"}).context["statuses"] == [
        ("waiting", "Waiting on prerequisites"),
        ("query_raised", "Query raised"),
        ("rejected", "Rejected"),
        ("approved", "Approved"),
    ]
    assert rows("ready") == [(["in_review"], ["approved"])]
    assert rows("decided") == []

    workflows.decide(
        m1,
        reviewer,
        action="query",
        note="Which test cases cover consent expiry?",
    )
    assert rows("ready") == []
    assert rows("decided") == [([], ["approved", "query_raised"])]

    workflows.reply_query(
        m1.queries.get(),
        environment["applicant"],
        "Test cases 4 and 7 cover consent expiry.",
    )
    assert rows("ready") == [(["in_review"], ["approved"])]
    assert rows("decided") == []


def test_an_open_query_sets_the_whole_product_aside(environment, client):
    """M2 and M3 each build on M1 alone, so either can be decided. A query on
    M2 still moves the product to Done until the integrator replies."""
    approve(environment, "m1")
    m2 = submit(environment, "m2")
    m3 = submit(environment, "m3")
    reviewer = environment["reviewer"]
    client.force_login(reviewer)
    url = reverse("experiences:queue")

    def rows(scope):
        response = client.get(url, {"scope": scope})
        return [
            (
                [item.queue_state for item in entry.pending_reviews],
                [item.queue_state for item in entry.done_reviews],
            )
            for entry in response.context["page"]
        ]

    assert rows("ready") == [(["in_review", "in_review"], ["approved", "approved"])]

    workflows.decide(
        m2,
        reviewer,
        action="query",
        note="Which test cases cover the HIP data push?",
    )
    assert rows("ready") == []
    # Done leaves out M3, which the query sets aside; All shows it.
    assert rows("decided") == [([], ["approved", "approved", "query_raised"])]
    assert rows("all") == [
        ([], ["approved", "approved", "query_raised", "in_review"]),
    ]
    entry = client.get(url, {"scope": "all", "status": "in_review"}).context["page"][0]
    assert entry.matching_reviews == [m3]
    # A search that finds M3 alone still lists the product under Done, though
    # the query that sets it aside falls outside the search.
    response = client.get(url, {"scope": "decided", "q": m3.application.reference})
    assert response.context["stage_counts"] == {"ready": 0, "decided": 1, "all": 1}
    assert [row.matching_reviews for row in response.context["page"]] == [[m3]]
    dashboard = client.get(reverse("experiences:assess-dashboard")).context
    abdm = next(card for card in dashboard["track_cards"] if card["title"] == "ABDM")
    assert abdm["tiles"][0]["count"] == 0

    workflows.reply_query(
        m2.queries.get(),
        environment["applicant"],
        "Test cases 2 and 5 cover the HIP data push.",
    )
    assert rows("ready") == [(["in_review", "in_review"], ["approved", "approved"])]


def test_the_legend_names_only_the_states_a_tab_can_show(environment, client):
    """Each tab's legend names only what its chips can show: Pending what can
    be decided and what is decided, Done everything else, and All the lot."""
    submit(environment, "m1")
    claims = submit_claims(environment, "m1")
    reviewer = environment["reviewer"]
    workflows.decide(
        claims,
        reviewer,
        action="query",
        note="Which test cases cover the claims flow?",
    )
    client.force_login(reviewer)
    url = reverse("experiences:queue")
    states = ("in_review", "blocked", "query_raised", "rejected", "approved")

    def legend(scope):
        html = client.get(url, {"scope": scope}).content.decode()
        return [
            state for state in states if f"ui-queue-chip--{state} font-medium" in html
        ]

    assert legend("ready") == ["in_review", "rejected", "approved"]
    assert legend("decided") == ["blocked", "query_raised", "rejected", "approved"]
    assert legend("all") == list(states)


def test_organisation_verification_merges_into_its_product_entry(
    client,
    review_item,
    owner_membership,
):
    organisation = workflows.organisation_review(
        owner_membership.organisation,
        owner_membership.user,
    )
    organisation, form, saved = workflows.save_review_form(
        organisation,
        owner_membership.user,
        data={"supplier_name": "Pump supplier"},
        submit=True,
    )
    assert saved, form.errors
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(reverse("experiences:queue"), {"scope": "all"})

    assert response.context["page"].paginator.count == 1
    assert response.context["stage_counts"]["all"] == 1
    entry = response.context["page"][0]
    assert entry.product == review_item.product
    assert entry.reviews[0] == organisation
    assert set(entry.matching_reviews) == {organisation, review_item}
    assert b"Organisation" in response.content
    assert organisation.get_absolute_url().encode() not in response.content

    # An organisation-only filter still opens the complete product review.
    response = client.get(
        reverse("experiences:queue"),
        {"item": "organisation_verification", "q": entry.reference},
    )
    assert response.context["page"].paginator.count == 1
    assert response.context["page"][0].matching_reviews == [organisation]
    assert response.context["page"][0].url == entry.url


def test_pagination_never_splits_products_or_merges_similar_product_names(
    client,
    review_item,
    owner_membership,
):
    items = [review_item]
    for _index in range(10):
        product, form = workflows.register_product(
            owner_membership.organisation,
            owner_membership.user,
            data={
                "equipment_name": "Water pump",
                "summary": "Another product with the same name",
                "checks": ["Quality:inspection", "Quality:release"],
            },
        )
        assert product, form.errors
        inspection = product.milestones.get(
            key="inspection",
        ).application.review_item
        inspection, form, saved = workflows.save_review_form(
            inspection,
            owner_membership.user,
            data={"report_reference": product.reference, "score": 90},
            submit=True,
        )
        assert saved, form.errors
        items.append(inspection)
    for item in items:
        submit_release(item, owner_membership.user)

    organisation = submit_organisation(owner_membership)
    client.force_login(ReviewerFactory(is_nha_team=True))
    first = client.get(reverse("experiences:queue"), {"scope": "all"})
    second = client.get(reverse("experiences:queue"), {"scope": "all", "page": 2})
    first_page, second_page = first.context["page"], second.context["page"]

    assert first_page.paginator.count == 11
    assert first.context["stage_counts"] == {
        "ready": 11,
        "decided": 0,
        "all": 11,
    }
    assert len(first_page) == 10
    assert len(second_page) == 1
    assert {entry.product_id for entry in first_page}.isdisjoint(
        [entry.product_id for entry in second_page],
    )
    for entry in [*first_page, *second_page]:
        assert len(entry.reviews) == len(entry.matching_reviews) == 3
        assert entry.reviews[0] == organisation
    organisations = client.get(
        reverse("experiences:queue"),
        {"item": "organisation_verification"},
    )
    assert organisations.context["page"].paginator.count == 11
    assert organisations.context["stage_counts"]["ready"] == 11
    assert all(
        entry.matching_reviews == [organisation]
        for entry in organisations.context["page"]
    )
    assert b'aria-label="Queue pagination"' in first.content
    assert b"scope=all" in first.content


def test_sort_uses_each_products_matching_request_dates(
    client,
    review_item,
    owner_membership,
):
    submit_organisation(MembershipFactory(role="owner"))
    ReviewItem.objects.filter(pk=review_item.pk).update(
        submitted_at=timezone.now() - timedelta(days=2),
    )
    client.force_login(ReviewerFactory(is_nha_team=True))
    url = reverse("experiences:queue")

    newest = client.get(url, {"scope": "all"}).context["page"]
    oldest = client.get(url, {"scope": "all", "sort": "oldest"}).context["page"]
    assert [entry.product_id for entry in newest] == [None, review_item.product_id]
    assert [entry.product_id for entry in oldest] == [review_item.product_id, None]


def submit_organisation(membership):
    organisation = workflows.organisation_review(
        membership.organisation,
        membership.user,
    )
    organisation, form, saved = workflows.save_review_form(
        organisation,
        membership.user,
        data={"supplier_name": "Pump supplier"},
        submit=True,
    )
    assert saved, form.errors
    return organisation


def test_organisation_without_products_remains_a_standalone_entry(
    client,
    review_item,
):
    organisation = submit_organisation(MembershipFactory(role="owner"))
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(
        reverse("experiences:queue"),
        {"item": "organisation_verification"},
    )
    assert response.context["page"].paginator.count == 1
    entry = response.context["page"][0]
    assert entry.product is None
    assert entry.reviews == [organisation]
    assert entry.url == organisation.get_absolute_url()


def test_organisation_only_submission_opens_a_product_with_draft_milestones(
    client,
    review_item,
    owner_membership,
):
    organisation = submit_organisation(owner_membership)
    workflows.withdraw(review_item, owner_membership.user)
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(reverse("experiences:queue"))
    assert response.context["page"].paginator.count == 1
    entry = response.context["page"][0]
    assert entry.product == review_item.product
    assert entry.matching_reviews == [organisation]
    assert entry.reviews == [organisation]
    assert b"Not submitted" not in response.content


def test_category_reviewer_never_sees_organisation_verification(
    client,
    review_item,
    owner_membership,
):
    organisation = submit_organisation(owner_membership)
    reviewer = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=reviewer,
        program="supplier_quality",
        area="review",
        category="Quality",
    )
    client.force_login(reviewer)
    response = client.get(reverse("experiences:queue"), {"scope": "all"})
    assert response.context["page"].paginator.count == 1
    assert organisation not in response.context["page"][0].reviews
    assert organisation not in response.context["page"][0].matching_reviews
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "all", "item": "organisation_verification"},
    )
    assert response.context["page"].paginator.count == 0


def test_search_for_one_product_leaves_out_its_organisations_other_products(
    client,
    review_item,
    owner_membership,
):
    organisation = submit_organisation(owner_membership)
    compressor, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Air compressor",
            "summary": "A second product of the same organisation",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert compressor, form.errors
    client.force_login(ReviewerFactory(is_nha_team=True))
    url = reverse("experiences:queue")

    # The organisation verification sits on both products. Searching for one
    # must not bring in the other.
    for search in ("Air compressor", compressor.reference):
        page = client.get(url, {"q": search}).context["page"]
        assert [entry.product for entry in page] == [compressor]
        assert page[0].matching_reviews == [organisation]
    page = client.get(url, {"q": review_item.product.reference}).context["page"]
    assert [entry.product for entry in page] == [review_item.product]
    assert page[0].matching_reviews == [organisation, review_item]


def test_search_finds_what_the_queue_shows(client, review_item, owner_membership):
    submit_organisation(owner_membership)
    Organisation.objects.filter(pk=owner_membership.organisation_id).update(
        legal_name="Sunrise Medical Devices Private Limited",
    )
    standalone = submit_organisation(MembershipFactory(role="owner"))
    client.force_login(ReviewerFactory(is_nha_team=True))
    url = reverse("experiences:queue")

    def found(search):
        page = client.get(url, {"q": search}).context["page"]
        return [entry.reference for entry in page]

    product = review_item.product.reference
    assert found(f"  {product} ") == [product]
    # The queue names an organisation by its legal name when it has one.
    assert found("medical devices") == [product]
    # The reference on a standalone organisation verification, and a request's
    # own reference as its review page shows it.
    assert found(standalone.reference) == [standalone.reference]
    assert found(review_item.reference.lower()) == [product]
