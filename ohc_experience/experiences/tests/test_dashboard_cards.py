from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_reject_reasons import DOCUMENTS
from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import decide
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_milestone
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_renewal
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.abdm.tests.test_workflow import submit_claims
from ohc_experience.abdm.wasa import current_wasa
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import OUTCOME_EXPIRING_DAYS
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.organisations.models import GOVERNMENT
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PROGRAMMES = ("", "HIE-CM", "PHR", "UHI", "NHCX")


def dashboard(client, track=""):
    return client.get(reverse("experiences:assess-dashboard"), {"track": track}).context


def rows(client, url):
    """How many rows the page a dashboard link opens lists."""
    context = client.get(url).context
    if "stage_counts" in context:
        return context["stage_counts"][context["queue_scope"]]
    return context["products"].paginator.count


def split_figures(split):
    return (
        split["pending"]["count"],
        split["new"]["count"],
        split["answered"]["count"],
    )


def links(context):
    for split in (context["onboarding_queue"], context["exit"]):
        yield split["pending"]
        yield split["new"]
        yield split["answered"]
    yield from context["standings"].values()


def test_every_figure_is_the_row_count_of_the_page_it_opens(
    environment,  # noqa: F811
    client,
):
    approve(environment, "m1")
    m2 = submit(environment, "m2")
    reviewer = environment["reviewer"]
    services.decide(m2, reviewer, action="query", note="Which tests cover it?")
    services.reply_query(m2.queries.get(), environment["applicant"], "Cases 2 and 5.")
    submit(environment, "uhi1")
    locker = submit(environment, "p1")
    services.decide(locker, reviewer, action="reject", reason=DOCUMENTS)
    submit_claims(environment, "m1")
    submit_claims(environment, "m3")
    submit_claims(environment, "nhcx_payer")
    client.force_login(environment["admin"])

    # Pending, New and Clarification received. M2 is back with its reply and the
    # claims product's M1 is new. NHCX waits on M1 and M3, so nothing in it can
    # be decided yet.
    assert {
        track: split_figures(dashboard(client, track)["exit"]) for track in PROGRAMMES
    } == {
        "": (2, 1, 1),
        "HIE-CM": (2, 1, 1),
        "PHR": (0, 0, 0),
        "UHI": (0, 0, 0),
        "NHCX": (0, 0, 0),
    }
    for track in PROGRAMMES:
        context = dashboard(client, track)
        for link in links(context):
            assert rows(client, link["url"]) == link["count"], (track, link["label"])
        totals = {choice["code"]: choice["total"] for choice in context["programmes"]}
        assert totals[track] == context["total"] == sum(context["pending"].values())


def test_an_organisation_counts_in_the_programmes_of_its_products(
    environment,  # noqa: F811
    client,
):
    """Its verification joins the queue with its first product, and counts in
    every programme that product applied for."""
    owner = UserFactory()
    org = Organisation.objects.create(name="Second health systems")
    Membership.objects.create(organisation=org, user=owner, role="owner")
    item, form, saved = services.save_review_form(
        services.organisation_review(org, owner),
        owner,
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    assert saved, form.errors
    client.force_login(environment["admin"])

    def figures(track=""):
        context = dashboard(client, track)
        for link in links(context):
            assert rows(client, link["url"]) == link["count"], link["label"]
        return split_figures(context["onboarding_queue"])

    assert figures() == (0, 0, 0)

    product, form = services.register_product(org, owner, data=product_data("New"))
    assert product, form.errors
    assert figures() == figures("HIE-CM") == figures("UHI") == (1, 1, 0)
    assert figures("PHR") == (0, 0, 0)

    services.decide(item, environment["reviewer"], action="query", note="Which PAN?")
    assert figures() == (0, 0, 0)
    services.reply_query(item.queries.get(), owner, "The one on the certificate.")
    assert figures() == (1, 0, 1)


def test_wasa_figures_follow_each_product_s_current_certificate(
    environment,  # noqa: F811
    client,
):
    product = environment["product"]
    decide(environment, request_milestone(environment))
    client.force_login(environment["admin"])

    def shown(track=""):
        context = dashboard(client, track)
        for link in links(context):
            assert rows(client, link["url"]) == link["count"], link["label"]
        return {
            key: (standing["count"], standing["government"], standing["private"])
            for key, standing in context["standings"].items()
        }

    # The milestone's certificate runs out in 30 days.
    assert shown() == {
        "expired": (0, 0, 0),
        "expiring": (1, 0, 1),
        "active": (0, 0, 0),
    }
    assert shown("HIE-CM") == shown()
    assert shown("NHCX")["expiring"] == (0, 0, 0)

    ProductOutcome.objects.filter(product=product).update(
        valid_until=timezone.localdate() - timedelta(days=1),
    )
    Organisation.objects.filter(pk=product.organisation_id).update(
        entity_type=GOVERNMENT,
    )
    assert shown()["expired"] == (1, 1, 0)

    renewal = request_renewal(
        environment,
        certificate=certificate_data(expires_in=200, audited_ago=1),
    )
    context = dashboard(client)
    assert context["pending"]["renewals"] == 1
    assert rows(client, context["renewals_url"]) == 1
    decide(environment, renewal)

    # The renewal's later audit makes it the current certificate.
    assert shown()["expired"] == (0, 0, 0)
    context = dashboard(client)
    assert context["standings"]["active"]["count"] == context["compliance"]["live"]
    response = client.get(context["standings"]["active"]["url"])
    [row] = response.context["products"]
    assert row.certificate_valid_until == current_wasa(product).valid_until


def test_a_clarification_card_adds_both_lists_and_opens_the_exits(
    environment,  # noqa: F811
    client,
):
    m1 = submit(environment, "m1")
    reviewer = environment["reviewer"]
    services.decide(m1, reviewer, action="query", note="Which tests cover it?")
    services.reply_query(m1.queries.get(), environment["applicant"], "Cases 2 and 5.")
    client.force_login(environment["admin"])

    context = dashboard(client)
    card = next(
        card
        for card in context["attention_cards"]
        if card["label"] == "Clarifications received"
    )

    assert card["count"] == 1
    assert card["note"] == "Entities 0 · Exits 1"
    assert card["url"] == context["exit"]["answered"]["url"]


def test_wasa_leads_the_attention_cards_and_decisions_follow(
    environment,  # noqa: F811
    client,
):
    client.force_login(environment["admin"])

    labels = [card["label"] for card in dashboard(client)["attention_cards"]]

    assert labels == [
        "WASA expired",
        f"WASA expiring in {OUTCOME_EXPIRING_DAYS} days",
        "New applications to verify",
        "Clarifications received",
        "Exit requests to review",
    ]


@pytest.mark.parametrize("track", ["", "HIE-CM", "PHR", "UHI", "NHCX"])
def test_attention_cards_always_fill_their_rows(
    environment,  # noqa: F811
    client,
    track,
):
    client.force_login(environment["admin"])

    html = client.get(reverse("experiences:assess-dashboard"), {"track": track}).text

    columns = 2 * html.count("xl:col-span-2") + 3 * html.count("xl:col-span-3")
    assert columns % 6 == 0
