"""Forms open in chain order, and reviewers see what waits on what."""

# ruff: noqa: F811
import re
from importlib import import_module

import pytest
from django.apps import apps as registry
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.tests.test_workflow import approve_submitted
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import reverify
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ReviewItem

pytestmark = pytest.mark.django_db


def track_url(environment, code="HIE-CM"):
    return reverse(
        "experiences:track",
        args=[environment["workspace"].reference, code],
    )


def queue(client, **params):
    return list(client.get(reverse("experiences:queue"), params).context["page"])


def queue_text(client, **params):
    html = client.get(reverse("experiences:queue"), params).content.decode()
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ")


def test_a_milestone_opens_once_everything_before_it_is_submitted(environment):
    m2 = milestone(environment, "m2")
    for submit_form in (False, True):
        with pytest.raises(
            ValidationError,
            match=r"^\['M2 - HIP services opens once M1 - ABHA and identity is "
            r"submitted.'\]$",
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

    submit(environment)

    assert submit(environment, "m2").status == ReviewItem.Status.NEW
    assert submit(environment, "uhi1").status == ReviewItem.Status.NEW


def test_m4_opens_and_is_approved_before_m1_is_submitted(environment, client):
    client.force_login(environment["applicant"])
    html = client.get(track_url(environment), {"milestone": "m4"}).content.decode()

    assert "data-milestone-locked" not in html
    assert "data-review-form" in html

    m4 = submit(environment, "m4")

    assert milestone(environment).status == ReviewItem.Status.DRAFT
    assert workflows.pending_prerequisites(m4) == []
    approve_submitted(environment, "m4")
    assert milestone(environment, "m4").status == ReviewItem.Status.APPROVED


def test_a_sent_back_milestone_leaves_the_next_one_open(environment):
    """A reviewer's decision never closes a form the integrator is working in."""
    m1 = submit(environment)
    workflows.assign_review(m1, environment["admin"], environment["reviewer"])
    workflows.decide(
        m1,
        environment["reviewer"],
        action="send_back",
        note="Add the consent revocation scenarios.",
    )

    assert workflows.unsubmitted_prerequisites(milestone(environment, "m2")) == []
    assert submit(environment, "m2").status == ReviewItem.Status.NEW


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
            "Withdraw UHI1 - UHI participation, M3 - HIU services and M2 - HIP "
            "services first. They build on this request."
        ),
    ]

    for key in ("m3", "m2"):
        workflows.withdraw(milestone(environment, key), applicant)
    with pytest.raises(
        ValidationError,
        match=r"Withdraw UHI1 - UHI participation first\. It builds on this request\.",
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
        f'href="{track_url(environment)}?milestone=m1">M1 - ABHA and identity</a>'
        in html
    )
    assert "Locked · submit M1 first" in html
    assert "M3 HIU services</a> · submit M1 first" in html.replace(
        '<span class="font-mono">M3</span>',
        "M3",
    )

    submit(environment)
    html = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert "data-milestone-locked" not in html
    assert "data-review-form" in html


def test_withdrawing_names_the_requests_to_withdraw_first(environment, client):
    submit(environment)
    submit(environment, "m2")
    client.force_login(environment["applicant"])

    m1 = client.get(track_url(environment), {"milestone": "m1"}).content.decode()
    m2 = client.get(track_url(environment), {"milestone": "m2"}).content.decode()

    assert "data-withdraw-hold" in m1
    assert "Withdraw request</button>" not in m1
    assert re.search(
        rf'first withdraw <a [^>]*href="{track_url(environment)}\?milestone=m2">'
        r"M2 - HIP services</a>\. It builds on this request\.",
        m1,
    )
    assert "Withdraw request</button>" in m2


def test_the_queue_holds_waiting_requests_apart_from_ready_ones(environment, client):
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    uhi = submit(environment, "uhi1")
    locker = submit(environment, "locker1")
    client.force_login(environment["reviewer"])

    assert set(queue(client)) == {m1, locker}
    assert client.get(reverse("experiences:queue")).context["queue_scope"] == "ready"
    assert set(queue(client, scope="waiting")) == {m2, uhi}
    assert "2 waiting on this" in queue_text(client)
    assert "Waiting on M1 · new" in queue_text(client, scope="waiting")
    dashboard = client.get(reverse("experiences:assess-dashboard")).context
    assert (dashboard["ready_count"], dashboard["waiting_count"]) == (2, 2)

    approve_submitted(environment)

    assert set(queue(client)) == {m2, locker}
    assert queue(client, scope="waiting") == []
    uhi.refresh_from_db()
    assert uhi.status == ReviewItem.Status.APPROVED


def test_requests_wait_on_organisation_verification_too(environment, client):
    verification = reverify(environment)
    locker = submit(environment, "locker1")
    client.force_login(environment["reviewer"])

    assert queue(client) == [verification]
    assert queue(client, scope="waiting") == [locker]
    assert "Waiting on organisation verification · under review" in queue_text(
        client,
        scope="waiting",
    )


def test_the_waiting_filter_agrees_with_pending_prerequisites(environment):
    def agree():
        pending = ReviewItem.objects.filter(status__in=workflows.PENDING_STATUSES)
        return set(pending.filter(workflows.waiting_reviews())) == {
            item for item in pending if workflows.pending_prerequisites(item)
        }

    for key in ("m1", "m2", "m3", "m4", "uhi1", "locker1"):
        submit(environment, key)
    assert agree()
    verification = reverify(environment)
    assert agree()
    workflows.assign_review(verification, environment["admin"], environment["reviewer"])
    workflows.decide(verification, environment["reviewer"], action="approve")
    approve_submitted(environment)
    assert agree()
    approve_submitted(environment, "m2")
    assert agree()


def test_the_review_page_lists_the_requests_waiting_on_it(environment, client):
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    uhi = submit(environment, "uhi1")
    client.force_login(environment["reviewer"])

    html = client.get(m1.get_absolute_url()).content.decode()

    assert "2 requests wait on this one" in html
    assert 'aria-label="Waiting on this request"' in html
    assert f'href="{m2.get_absolute_url()}">M2 - HIP services</a>' in html
    assert f'href="{uhi.get_absolute_url()}">UHI1 - UHI participation</a>' in html

    approve_submitted(environment)

    html = client.get(m1.get_absolute_url()).content.decode()
    assert "wait on this one" not in html


def test_the_migration_moves_a_saved_m3_request_from_m2_to_m1(environment):
    """Requests created under the old catalog still depend on M2."""
    migration = import_module(
        "ohc_experience.experiences.migrations.0018_m3_builds_on_m1",
    )
    applications = {
        row.key: row.application_id
        for row in environment["workspace"].product.milestones.all()
    }
    saved = ApplicationDependency.objects.filter(application_id=applications["m3"])
    saved.delete()
    ApplicationDependency.objects.create(
        application_id=applications["m3"],
        depends_on_id=applications["m2"],
    )

    migration.forwards(registry, None)

    assert list(saved.values_list("depends_on_id", flat=True)) == [applications["m1"]]

    migration.backwards(registry, None)

    assert list(saved.values_list("depends_on_id", flat=True)) == [applications["m2"]]


def test_the_migration_drops_a_saved_m4_dependency_on_m3(environment):
    migration = import_module(
        "ohc_experience.experiences.migrations.0019_m4_has_no_prerequisite",
    )
    applications = {
        row.key: row.application_id
        for row in environment["workspace"].product.milestones.all()
    }
    saved = ApplicationDependency.objects.filter(application_id=applications["m4"])
    ApplicationDependency.objects.create(
        application_id=applications["m4"],
        depends_on_id=applications["m3"],
    )
    others = ApplicationDependency.objects.exclude(application_id=applications["m4"])
    unrelated = set(others.values_list("pk", flat=True))

    migration.forwards(registry, None)

    assert not saved.exists()
    assert set(others.values_list("pk", flat=True)) == unrelated

    migration.backwards(registry, None)

    assert list(saved.values_list("depends_on_id", flat=True)) == [applications["m3"]]
