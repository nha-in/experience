"""Forms open in chain order, and reviewers see what waits on what."""

# ruff: noqa: F811
import re

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve_submitted
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import phr_product
from ohc_experience.abdm.tests.test_workflow import reverify
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.organisations.models import GOVERNMENT

pytestmark = pytest.mark.django_db


def track_url(environment, code="HIE-CM"):
    return reverse(
        "experiences:track",
        args=[environment["product"].reference, code],
    )


def queue(client, **params):
    return [
        item
        for entry in client.get(reverse("experiences:queue"), params).context["page"]
        for item in entry.matching_reviews
    ]


def queue_text(client, **params):
    html = client.get(reverse("experiences:queue"), params).content.decode()
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ")


def test_a_milestone_opens_once_everything_before_it_is_submitted(environment):
    m2 = milestone(environment, "m2")
    for submit_form in (False, True):
        with pytest.raises(
            ValidationError,
            match=r"^\['M2 - Health Information Provider Services opens once "
            r"M1 - ABHA Creation and Verification is submitted.'\]$",
        ):
            workflows.save_review_form(
                m2,
                environment["applicant"],
                data=evidence_data(),
                files=files(),
                submit=submit_form,
            )
    with pytest.raises(ValidationError, match="cannot be reused"):
        workflows.reuse_evidence(m2, environment["applicant"])
    assert workflows.milestone_unavailable(milestone(environment, "uhi1")) == (
        "UHI - UHI participation opens once "
        "M1 - ABHA Creation and Verification is submitted."
    )

    submit(environment)

    assert submit(environment, "m2").status == ReviewItem.Status.IN_REVIEW
    assert submit(environment, "uhi1").status == ReviewItem.Status.IN_REVIEW


def test_m4_waits_for_m1_unless_the_entity_is_a_government_body(environment, client):
    """A government body registers facilities under its own authority."""
    client.force_login(environment["applicant"])
    html = client.get(track_url(environment), {"milestone": "m4"}).content.decode()

    assert "data-milestone-locked" in html
    assert workflows.milestone_unavailable(milestone(environment, "m4")) == (
        "M4 - Register Healthcare Professionals and Facilities opens once "
        "M1 - ABHA Creation and Verification is submitted."
    )

    organisation = environment["org"]
    organisation.entity_type = GOVERNMENT
    organisation.save(update_fields=["entity_type"])
    government, form = workflows.register_product(
        organisation,
        environment["applicant"],
        data={
            **product_data("State facility registry"),
            "solution_type": ["other"],
            "solution_type_other": "Facility registry",
            "applied_milestones": ["HIE-CM:m4"],
        },
    )
    assert government, form.errors

    m4 = government.milestones.get(key="m4").application.review_item

    assert workflows.milestone_unavailable(m4) == ""
    assert workflows.pending_prerequisites(m4) == []


def reject(environment, key="m1"):
    item = milestone(environment, key)
    workflows.assign_review(item, environment["admin"], environment["reviewer"])
    workflows.decide(
        item,
        environment["reviewer"],
        action="reject",
        reason="Incomplete integration",
        note="Add the consent revocation scenarios.",
    )


def test_a_rejected_milestone_locks_the_next_one_until_it_is_resubmitted(
    environment,
):
    submit(environment)
    reject(environment)

    for submit_form in (False, True):
        with pytest.raises(
            ValidationError,
            match=r"^\['M2 - Health Information Provider Services opens once "
            r"M1 - ABHA Creation and Verification is resubmitted.'\]$",
        ):
            workflows.save_review_form(
                milestone(environment, "m2"),
                environment["applicant"],
                data=evidence_data(),
                files=files(),
                submit=submit_form,
            )
    assert workflows.milestone_unavailable(milestone(environment, "p2")) == (
        "P2 - Consents Management opens once P1 - Registration and login is submitted."
    )

    submit(environment)

    assert submit(environment, "m2").status == ReviewItem.Status.IN_REVIEW


def test_the_track_page_asks_for_a_rejected_milestone_to_be_resubmitted(
    environment,
    client,
):
    submit(environment)
    reject(environment)
    client.force_login(environment["applicant"])

    html = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert "data-milestone-locked" in html
    assert "data-review-form" not in html
    assert "M1 - ABHA Creation and Verification</a> is resubmitted." in html
    tiles = milestone_tiles(html)
    assert tiles["M1"].startswith("M1 Rejected ")
    assert tiles["M2"].startswith("M2 Locked ")
    assert "Resubmit M1 first" in tiles["M2"]
    assert tiles["M3"].startswith("M3 Locked ")
    assert "Resubmit M1 first" in tiles["M3"]
    overview = client.get(
        reverse("experiences:overview", args=[environment["product"].reference]),
    ).content.decode()
    assert "Locked · resubmit M1 first" in overview


def test_a_request_is_withdrawn_only_after_everything_built_on_it(environment):
    applicant = environment["applicant"]
    submit(environment)
    submit(environment, "m2")
    submit(environment, "m3")
    submit(environment, "uhi1")

    with pytest.raises(ValidationError) as error:
        workflows.withdraw(milestone(environment), applicant)
    assert error.value.messages == [
        (
            "Withdraw UHI - UHI participation, M3 - Health Information User "
            "Services and M2 - Health Information Provider Services first. "
            "They build on this request."
        ),
    ]

    workflows.withdraw(milestone(environment, "m2"), applicant)
    workflows.withdraw(milestone(environment, "m3"), applicant)
    with pytest.raises(
        ValidationError,
        match=r"Withdraw UHI - UHI participation first\. It builds on this request\.",
    ):
        workflows.withdraw(milestone(environment), applicant)

    for key in ("uhi1", "m1"):
        workflows.withdraw(milestone(environment, key), applicant)

    assert milestone(environment).status == ReviewItem.Status.DRAFT
    assert workflows.milestone_unavailable(milestone(environment, "m2"))


def test_the_track_page_locks_a_milestone_and_links_what_opens_it(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    page = client.get(track_url(environment), {"milestone": "m2"})
    html = page.content.decode()

    assert "data-milestone-locked" in html
    assert "data-review-form" not in html
    assert (
        f'href="{track_url(environment)}?milestone=m1">'
        "M1 - ABHA Creation and Verification</a>" in html
    )
    tile = milestone_tiles(html)["M2"]
    assert tile.startswith("M2 Locked ")
    assert "Submit M1 first" in tile
    assert progress_lines(html) == [
        "Pending implementation: M1",
        "Submit M1 first: M2, M3, M4",
    ]

    submit(environment)
    html = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert "data-milestone-locked" not in html
    assert "data-review-form" in html


def progress_lines(html):
    """The track hero's progress lines, without the completion note that closes
    their paragraph."""
    hero = re.search(r'<p class="mt-2\.5[^"]*">(.*?)</p>', html, flags=re.S).group(1)
    *lines, _completion = hero.split("<br />")
    return [" ".join(re.sub(r"<[^>]+>", "", line).split()) for line in lines]


def test_the_track_hero_groups_what_is_left_by_what_it_waits_on(environment, client):
    """One line per state instead of one per milestone, the integrator's first."""
    submit(environment)
    approve_submitted(environment)
    submit(environment, "m2")
    client.force_login(environment["applicant"])

    html = client.get(track_url(environment)).content.decode()

    assert "of 4 milestones approved · 25% completed" in html
    assert progress_lines(html) == [
        "Pending implementation: M3, M4",
        "With the reviewer: M2",
    ]

    workflows.decide(
        milestone(environment, "m2"),
        environment["admin"],
        action="query",
        note="Name the consent scenarios you exercised.",
    )
    html = client.get(track_url(environment)).content.decode()

    assert progress_lines(html) == [
        "Action required from applicant: M2",
        "Pending implementation: M3, M4",
    ]


def test_only_the_abdm_track_page_leaves_out_its_description(environment, client):
    """NHA asked for the line under the HIE-CM title to go; UHI keeps its own."""
    client.force_login(environment["applicant"])

    abdm = client.get(track_url(environment)).content.decode()
    uhi = client.get(track_url(environment, "UHI")).content.decode()

    assert 'class="ui-hero-lede"' not in abdm
    assert TRACK_MAP["HIE-CM"].description not in abdm
    assert f'<p class="ui-hero-lede">{TRACK_MAP["UHI"].description}</p>' in uhi


def milestone_tiles(html):
    """The text of each milestone tile on a track page, by milestone code."""
    texts = [
        " ".join(re.sub(r"<[^>]+>", " ", tile).split())
        for tile in re.findall(r'<a class="ui-milestone-tile .*?</a>', html, flags=re.S)
    ]
    return {text.split()[0]: text for text in texts}


def test_a_milestone_tile_names_the_milestone_it_needs(environment, client):
    client.force_login(environment["applicant"])

    html = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert milestone_tiles(html) == {
        "M1": (
            "M1 Open ABHA Creation and Verification Shared with UHI "
            "Pending Implementation"
        ),
        "M2": (
            "M2 Locked Health Information Provider Services Shared with UHI "
            "Submit M1 first Requires completion of M1"
        ),
        "M3": (
            "M3 Locked Health Information User Services Submit M1 first "
            "Requires completion of M1"
        ),
        "M4": (
            "M4 Locked Register Healthcare Professionals and Facilities "
            "Submit M1 first Requires completion of M1"
        ),
    }
    assert "ui-milestone-tile-needs--locked" in html

    uhi = milestone_tiles(client.get(track_url(environment, "UHI")).content.decode())

    assert "Requires completion of" not in uhi["M1"]
    assert uhi["M2"].endswith("Requires completion of M1")
    assert uhi["UHI"].endswith("Requires completion of M1")

    submit(environment)
    html = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert milestone_tiles(html)["M2"].endswith("Requires completion of M1")
    assert "ui-milestone-tile-needs--locked" not in html


def test_withdrawing_names_the_requests_to_withdraw_first(environment, client):
    submit(environment)
    submit(environment, "m2")
    client.force_login(environment["applicant"])

    m1 = client.get(track_url(environment), {"milestone": "m1"}).content.decode()
    m2 = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert "data-withdraw-hold" in m1
    assert "Withdraw request</button>" not in m1
    # Underlined, as a link in an alert is, so it does not read as plain text.
    assert re.search(
        r'first withdraw <a class="font-semibold underline underline-offset-4"\s+'
        rf'href="{track_url(environment)}\?milestone=m2">'
        r"M2 - Health Information Provider Services</a>\. It builds on this request\.",
        m1,
    )
    assert "Withdraw request</button>" in m2


def test_pending_lists_waiting_requests_beside_ready_ones(environment, client):
    """A milestone submitted after the one it builds on stays on its product's
    entry, but only All shows it, marked as waiting: Pending lists only what can
    be decided now, and what is already decided."""
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    uhi = submit(environment, "uhi1")
    locker = submit(environment, "p1")
    client.force_login(environment["reviewer"])

    assert set(queue(client)) == {m1, locker}
    response = client.get(reverse("experiences:queue"))
    assert response.context["queue_scope"] == "ready"
    assert {m2, uhi} <= {
        item for entry in response.context["page"] for item in entry.reviews
    }
    assert "2 waiting on this" in queue_text(client)
    assert "Waiting on M1 · under review" not in queue_text(client)
    assert "Waiting on M1 · under review" in queue_text(client, scope="all")
    dashboard = client.get(reverse("experiences:assess-dashboard")).context
    pending = {
        card["title"]: card["tiles"][0]["count"] for card in dashboard["track_cards"]
    }
    # UHI waits on M1, so nothing in it can be decided yet.
    assert pending == {"HIE-CM": 1, "PHR": 1, "UHI": 0, "NHCX": 0}

    approve_submitted(environment)

    assert set(queue(client)) == {m2, locker}
    uhi.refresh_from_db()
    assert uhi.status == ReviewItem.Status.APPROVED

    approve_submitted(environment, "m2")

    assert set(queue(client)) == {locker}


def test_requests_wait_on_organisation_verification_too(environment, client):
    verification = reverify(environment)
    locker = submit(environment, "p1")
    client.force_login(environment["reviewer"])

    # P1 waits on the verification, so only the verification can be decided
    # now: it is pending under Organisations, and P1's product is done.
    assert queue(client) == []
    page = client.get(reverse("experiences:queue"), {"scope": "decided"})
    assert locker in {item for entry in page.context["page"] for item in entry.reviews}
    organisations = client.get(reverse("experiences:queue"), {"kind": "organisations"})
    assert [entry.review for entry in organisations.context["page"]] == [verification]
    assert [entry.held for entry in organisations.context["page"]] == [1]
    assert b"1 request waiting on this verification" in organisations.content
    assert "Waiting on organisation verification · under review" in queue_text(
        client,
        scope="all",
    )


def test_the_waiting_filter_agrees_with_pending_prerequisites(environment):
    def agree():
        pending = ReviewItem.objects.filter(status__in=workflows.PENDING_STATUSES)
        return set(pending.filter(workflows.waiting_reviews())) == {
            item for item in pending if workflows.pending_prerequisites(item)
        }

    for key in ("m1", "m2", "m3", "m4", "uhi1", "p1"):
        submit(environment, key)
    assert agree()
    verification = reverify(environment)
    assert agree()
    workflows.assign_review(verification, environment["admin"], environment["reviewer"])
    workflows.decide(
        verification,
        environment["reviewer"],
        action="approve",
        note="Organisation identity verified.",
    )
    approve_submitted(environment)
    assert agree()
    approve_submitted(environment, "m2")
    assert agree()


def test_p4_waits_on_p1_p2_and_p3_together(environment, client):
    """The locker builds on the whole PHR sequence, not just its first step."""
    product = phr_product(environment)
    client.force_login(environment["applicant"])
    locker = product.milestones.get(key="p4").application

    assert set(
        ApplicationDependency.objects.filter(application=locker).values_list(
            "depends_on__metadata__milestone",
            flat=True,
        ),
    ) == {"p1", "p2", "p3"}
    tiles = milestone_tiles(
        client.get(
            track_url({**environment, "product": product}, "PHR"),
        ).content.decode(),
    )
    assert "Requires completion of P1, P2 and P3" in tiles["P4"]
