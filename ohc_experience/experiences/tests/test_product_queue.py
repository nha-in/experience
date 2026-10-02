# ruff: noqa: F811, PLR2004
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
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
    assert entry.matching_reviews == [m1, m2]
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


def test_queue_chips_put_open_requests_first_and_approved_ones_below(
    environment,
    client,
):
    submit(environment, "m1")
    submit(environment, "m2")
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")

    def rows(response):
        html = response.content.decode()
        open_row = html[html.index("data-queue-open") : html.index("data-queue-done")]
        done_row = html[html.index("data-queue-done") :].split("</ul>", 1)[0]
        return " ".join(open_row.split()), " ".join(done_row.split())

    for scope in ("all", "ready"):
        response = client.get(url, {"scope": scope})
        entry = response.context["page"][0]
        assert [item.queue_state for item in entry.open_reviews] == ["new", "blocked"]
        assert [item.queue_state for item in entry.approved] == ["approved"]
        open_row, done_row = rows(response)
        assert "M2 waiting on M1" in open_row
        assert "ui-queue-chip--approved" not in open_row
        assert "ui-queue-chip--approved" in done_row
        assert "of 3 approved" not in done_row


def test_filters_select_products_and_narrow_their_chips_to_matching_requests(
    client,
    review_item,
    owner_membership,
):
    release = submit_release(review_item, owner_membership.user)
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(release, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    response = client.get(
        reverse("experiences:queue"),
        {
            "scope": "ready",
            "item": "Quality",
            "status": "in_review",
            "assignee": "me",
            "q": release.application.reference,
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.context["page"].paginator.count == 1
    entry = response.context["page"][0]
    assert entry.reviews == [release, review_item]
    assert entry.matching_reviews == [release]
    assert entry.assignees == [reviewer]
    assert response.context["stage_counts"] == {
        "ready": 1,
        "decided": 0,
        "all": 1,
    }
    assert b"Waiting on INS" in response.content
    compact = " ".join(response.content.decode().split())
    assert 'title="REL Release · Under review · waiting on INS"' in compact
    assert 'title="INS Inspection' not in compact
    assert "REL waiting on INS" in compact
    assert "data-queue-done" not in compact


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
