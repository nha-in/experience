from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from ohc_experience.abdm.tests.test_reject_reasons import DOCUMENTS
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.abdm.tests.test_workflow import submit_claims
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.views import _last_twelve_months
from ohc_experience.experiences.views import _status_card

pytestmark = pytest.mark.django_db


def queue_rows(client, url):
    return client.get(url).context["page"].paginator.count


def figures(card):
    return card["total"], [tile["count"] for tile in card["tiles"]]


def test_every_dashboard_figure_is_the_row_count_of_the_queue_it_opens(
    environment,  # noqa: F811
    client,
):
    approve(environment, "m1")
    submit(environment, "m2")
    submit(environment, "uhi1")
    locker = submit(environment, "p1")
    services.assign_review(locker, environment["admin"], environment["reviewer"])
    services.decide(locker, environment["reviewer"], action="reject", reason=DOCUMENTS)
    submit_claims(environment, "m1")
    submit_claims(environment, "m3")
    submit_claims(environment, "nhcx_payer")
    client.force_login(environment["admin"])

    response = client.get(reverse("experiences:assess-dashboard"))
    cards = [response.context["sandbox_card"], *response.context["track_cards"]]

    # Total, then Pending, Rejected and Approved. The organisation's
    # verification is a row on each of its three products, as in the queue.
    assert {card["title"]: figures(card) for card in cards} == {
        "Sandbox access": (3, [0, 0, 3]),
        "ABDM": (2, [2, 0, 1]),
        "PHR": (1, [0, 1, 0]),
        "UHI": (1, [0, 0, 1]),
        "NHCX": (1, [1, 0, 0]),
    }
    # UHI is recorded without a reviewer, and that counts as an approval.
    assert {
        card["title"]: (card["this_month"]["approved"], card["this_month"]["rejected"])
        for card in cards
    } == {
        "Sandbox access": (1, 0),
        "ABDM": (1, 0),
        "PHR": (0, 1),
        "UHI": (1, 0),
        "NHCX": (0, 0),
    }
    html = response.content.decode()
    for card in cards:
        assert f'href="{escape(card["url"])}"' in html
        assert queue_rows(client, card["url"]) == card["total"], card["title"]
        for tile in card["tiles"]:
            assert f'href="{escape(tile["url"])}"' in html
            assert queue_rows(client, tile["url"]) == tile["count"], (
                card["title"],
                tile["label"],
            )


def test_a_decision_stays_in_the_month_it_was_made(environment):  # noqa: F811
    locker = submit(environment, "p1")
    services.assign_review(locker, environment["admin"], environment["reviewer"])
    services.decide(locker, environment["reviewer"], action="reject", reason=DOCUMENTS)

    def rejected_by_month(months_later):
        today = timezone.localdate().replace(day=1) + timedelta(days=31 * months_later)
        months = _last_twelve_months(today)
        card = _status_card(environment["admin"], "PHR", "PHR", "", months)
        return [month["rejected"] for month in card["months"]]

    assert rejected_by_month(0) == [0] * 11 + [1]
    assert rejected_by_month(3) == [0] * 8 + [1] + [0] * 3
    assert rejected_by_month(12) == [0] * 12
