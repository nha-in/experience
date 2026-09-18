# ruff: noqa: F811, PLR2004
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import ReviewItem

pytestmark = pytest.mark.django_db


def revisions(*items):
    return {str(item.pk): str(item.selected_submission_id or "") for item in items}


def sandbox_dates():
    today = timezone.localdate()
    return {
        "start_date": (today - timedelta(days=20)).isoformat(),
        "end_date": (today - timedelta(days=2)).isoformat(),
    }


def inherit_snapshot(target, snapshot):
    """The pin materialize_application_forms gives a newly added milestone."""
    target.selected_submission = snapshot
    target.save(update_fields=["selected_submission"])
    ApplicationFormUse.objects.filter(application=target.application).update(
        selected_submission=snapshot,
    )


def submit_group(environment, current, *additional, data=None, uploads=None, **kwargs):
    submitted = evidence_data() if data is None else data.copy()
    for index, target in enumerate(additional):
        for key, value in {
            "start_date": (
                timezone.localdate() - timedelta(days=30 + index)
            ).isoformat(),
            "end_date": (timezone.localdate() - timedelta(days=3 + index)).isoformat(),
        }.items():
            submitted.setdefault(f"milestone_{target.pk}_{key}", value)
    return workflows.save_review_forms(
        current,
        environment["applicant"],
        data=submitted,
        files=files() if uploads is None else uploads,
        expected_revision=current.selected_submission_id or "",
        additional_revisions=revisions(*additional),
        **kwargs,
    )


def assert_fresh(*items):
    for item in items:
        item.refresh_from_db()
        assert item.status == ReviewItem.Status.DRAFT
        assert item.selected_submission_id is None
        assert item.submitted_at is None


def test_targets_include_compatible_locked_siblings_but_not_distinct_forms(environment):
    current = milestone(environment)
    targets = workflows.submission_targets(current)
    assert milestone(environment, "m2") in targets
    assert milestone(environment, "m3") in targets
    assert milestone(environment, "m4") in targets
    assert milestone(environment, "phr1") not in targets
    assert milestone(environment, "locker1") not in targets
    assert milestone(environment, "uhi1") not in targets
    assert current not in targets
    assert workflows.milestone_unavailable(milestone(environment, "m2"))


def test_targets_exclude_saved_work_and_its_unavailable_dependants(environment):
    m1, form, saved = workflows.save_review_form(
        milestone(environment),
        environment["applicant"],
        data={"start_date": timezone.localdate()},
    )
    assert saved, form.errors
    targets = workflows.submission_targets(milestone(environment, "m4"))
    assert m1 not in targets
    assert milestone(environment, "m2") not in targets
    assert milestone(environment, "m3") not in targets
    assert milestone(environment, "m2") in workflows.submission_targets(m1)


def test_targets_exclude_disabled_milestones(environment):
    m2 = milestone(environment, "m2")
    m2.application.milestone.enabled = False
    m2.application.milestone.save(update_fields=["enabled"])
    assert m2 not in workflows.submission_targets(milestone(environment))


def test_group_submits_chain_with_independent_snapshots_and_one_upload_set(
    environment,
    django_capture_on_commit_callbacks,
):
    m1, m2, m3 = [milestone(environment, key) for key in ("m1", "m2", "m3")]
    with (
        patch.object(workflows, "notify_review") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        current, form, saved = submit_group(environment, m1, m3, m2)
        assert saved, form.errors
        notify.assert_not_called()
    assert len(callbacks) == notify.call_count == 3
    assert current.pk == m1.pk
    snapshots, attachment_ids, paths = [], [], []
    for item in (m1, m2, m3):
        item.refresh_from_db()
        assert item.status == ReviewItem.Status.NEW
        assert item.application.status == "under_review"
        snapshot = item.selected_submission
        assert snapshot.origin_application_id == item.application_id
        assert snapshot.status == "completed"
        assert item.application.form_uses.get().selected_submission_id == snapshot.pk
        snapshots.append(snapshot.pk)
        attachment_ids.append(set(snapshot.attachments.values_list("pk", flat=True)))
        paths.append(set(snapshot.attachments.values_list("file", flat=True)))
        assert snapshot.attachments.count() == 4
    assert len(set(snapshots)) == 3
    assert attachment_ids[0].isdisjoint(attachment_ids[1])
    assert paths[0] == paths[1] == paths[2]
    assert (
        len({item.selected_submission.data["start_date"] for item in (m1, m2, m3)}) == 3
    )
    assert (
        len({item.selected_submission.data["end_date"] for item in (m1, m2, m3)}) == 3
    )
    assert milestone(environment, "uhi1").status == ReviewItem.Status.DRAFT


def test_group_submits_prerequisite_before_current_when_explicitly_selected(
    environment,
):
    m1, m2 = milestone(environment), milestone(environment, "m2")
    current, form, saved = submit_group(environment, m2, m1)
    assert saved, form.errors
    assert current.pk == m2.pk
    assert list(
        AuditEvent.objects.filter(
            item__in=[m1, m2],
            action="Requested review",
        )
        .order_by("pk")
        .values_list("item_id", flat=True),
    ) == [m1.pk, m2.pk]


def test_missing_unsubmitted_prerequisite_does_not_submit_anything(environment):
    current = milestone(environment, "m4")
    m2 = milestone(environment, "m2")
    with pytest.raises(ValidationError, match="M1"):
        submit_group(environment, current, m2)
    assert_fresh(current, m2)


@pytest.mark.parametrize("missing_uploads", [False, True])
def test_invalid_current_form_returns_errors_without_any_saved_changes(
    environment,
    missing_uploads,
):
    m1, m2 = milestone(environment), milestone(environment, "m2")
    count = FormSubmission.objects.count()
    _, form, saved = submit_group(
        environment,
        m1,
        m2,
        data=evidence_data() if missing_uploads else {},
        uploads={} if missing_uploads else files(),
    )
    assert not saved
    assert (
        "wasa_certificate" in form.errors
        if missing_uploads
        else "start_date" in form.errors
    )
    assert FormSubmission.objects.count() == count
    assert_fresh(m1, m2)


def test_additional_saved_draft_is_never_overwritten(environment):
    current = milestone(environment)
    target, form, saved = workflows.save_review_form(
        milestone(environment, "m4"),
        environment["applicant"],
        data={"start_date": timezone.localdate()},
    )
    assert saved, form.errors
    original = target.selected_submission_id
    with pytest.raises(ValidationError, match="saved work"):
        submit_group(environment, current, target)
    assert_fresh(current)
    target.refresh_from_db()
    assert target.selected_submission_id == original


def test_stale_target_revision_prevents_current_submission(environment):
    current, target = milestone(environment), milestone(environment, "m2")
    with pytest.raises(ValidationError, match="teammate updated"):
        workflows.save_review_forms(
            current,
            environment["applicant"],
            data=evidence_data(),
            files=files(),
            expected_revision="",
            additional_revisions={target.pk: "999"},
        )
    assert_fresh(current, target)


def test_stale_current_revision_prevents_group_submission(environment):
    current, target = milestone(environment), milestone(environment, "m2")
    with pytest.raises(ValidationError, match="teammate updated"):
        workflows.save_review_forms(
            current,
            environment["applicant"],
            data=evidence_data(),
            files=files(),
            expected_revision="999",
            additional_revisions=revisions(target),
        )
    assert_fresh(current, target)


def test_later_failure_rolls_back_snapshots_pins_audit_and_notices(
    environment,
    django_capture_on_commit_callbacks,
):
    current, target = milestone(environment), milestone(environment, "m2")
    original = workflows._complete_submission  # noqa: SLF001

    def fail_second(item, actor, **kwargs):
        if item.pk == target.pk:
            msg = "Later milestone failed."
            raise ValidationError(msg)
        original(item, actor, **kwargs)

    snapshot_count = FormSubmission.objects.count()
    audit_count = AuditEvent.objects.count()
    with (
        patch.object(workflows, "_complete_submission", side_effect=fail_second),
        patch.object(workflows, "notify_review") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(ValidationError, match="Later milestone failed"),
    ):
        submit_group(environment, current, target)
    assert not callbacks
    notify.assert_not_called()
    assert FormSubmission.objects.count() == snapshot_count
    assert AuditEvent.objects.count() == audit_count
    assert_fresh(current, target)
    assert current.application.form_uses.get().selected_submission_id is None
    assert target.application.form_uses.get().selected_submission_id is None


def test_final_attachment_replacements_and_removals_are_shared(environment):
    uploaded = files()
    uploaded.setlist("supporting_evidence", [pdf("remove-me.pdf")])
    current, form, saved = workflows.save_review_form(
        milestone(environment),
        environment["applicant"],
        data=evidence_data(),
        files=uploaded,
    )
    assert saved, form.errors
    old = current.selected_submission
    removed = old.attachments.get(original_name="remove-me.pdf")
    target = milestone(environment, "m2")
    _, form, saved = submit_group(
        environment,
        current,
        target,
        data={
            **evidence_data(),
            "remove_files__supporting_evidence": [str(removed.pk)],
        },
        uploads=MultiValueDict({"functional_report": [pdf("replacement.pdf")]}),
    )
    assert saved, form.errors
    for item in (current, target):
        item.refresh_from_db()
        names = set(
            item.selected_submission.attachments.values_list(
                "original_name",
                flat=True,
            ),
        )
        assert "replacement.pdf" in names
        assert "report.pdf" not in names
        assert "remove-me.pdf" not in names
    assert old.attachments.filter(original_name="remove-me.pdf").exists()


def test_pending_and_approved_submissions_cannot_join_as_targets(environment):
    source = approve(environment)
    pending = submit(environment, "m2")
    current = milestone(environment, "m3")
    for target in (source, pending):
        with pytest.raises(ValidationError, match="additional milestone changed"):
            submit_group(environment, current, target)
    assert_fresh(current)


def test_another_products_target_is_rejected(environment):
    other, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Another product"),
    )
    assert other, form.errors
    target = milestone({**environment, "workspace": other})
    current = milestone(environment)
    with pytest.raises(ValidationError, match="additional milestone changed"):
        submit_group(environment, current, target)
    assert_fresh(current, target)


def test_different_form_cannot_join_the_batch(environment):
    current = milestone(environment)
    target = milestone(environment, "uhi1")
    with pytest.raises(ValidationError, match="additional milestone changed"):
        submit_group(environment, current, target)
    assert_fresh(current, target)


@pytest.mark.parametrize("key", ["phr1", "locker1"])
def test_same_form_in_another_track_cannot_join_the_batch(environment, key):
    current = milestone(environment)
    target = milestone(environment, key)
    assert current.form_id == target.form_id
    with pytest.raises(ValidationError, match="additional milestone changed"):
        submit_group(environment, current, target)
    assert_fresh(current, target)


def test_all_additional_dates_are_validated_even_when_primary_is_invalid(environment):
    current, m2, m3 = [milestone(environment, key) for key in ("m1", "m2", "m3")]
    _, form, saved = workflows.save_review_forms(
        current,
        environment["applicant"],
        data={},
        files=files(),
        expected_revision="",
        additional_revisions=revisions(m2, m3),
    )
    assert not saved
    assert "start_date" in form.errors
    assert set(form.additional_forms) == {m2.pk, m3.pk}
    for additional in form.additional_forms.values():
        assert "start_date" in additional.errors
        assert "end_date" in additional.errors
    assert_fresh(current, m2, m3)


def test_additional_dates_are_required_and_never_inherited_from_primary(environment):
    current, target = milestone(environment), milestone(environment, "m2")
    _, form, saved = workflows.save_review_forms(
        current,
        environment["applicant"],
        data=evidence_data(),
        files=files(),
        expected_revision="",
        additional_revisions=revisions(target),
    )
    assert not saved
    additional = form.additional_forms[target.pk]
    assert additional.data["start_date"] == ""
    assert additional.data["end_date"] == ""
    assert "start_date" in additional.errors
    assert "end_date" in additional.errors
    assert_fresh(current, target)


@pytest.mark.parametrize("invalid", ["future_start", "future_end", "reversed"])
def test_invalid_additional_dates_prevent_every_submission(environment, invalid):
    current, target = milestone(environment), milestone(environment, "m2")
    today = timezone.localdate()
    dates = sandbox_dates()
    if invalid == "future_start":
        dates["start_date"] = (today + timedelta(days=1)).isoformat()
    elif invalid == "future_end":
        dates["end_date"] = (today + timedelta(days=1)).isoformat()
    else:
        dates["end_date"] = (today - timedelta(days=25)).isoformat()
    _, form, saved = submit_group(
        environment,
        current,
        target,
        data={
            **evidence_data(),
            **{f"milestone_{target.pk}_{key}": value for key, value in dates.items()},
        },
    )
    assert not saved
    assert form.additional_forms[target.pk].errors
    assert_fresh(current, target)


def test_cross_track_inherited_pin_does_not_prefill_answers_or_functional_files(
    environment,
):
    source = submit(environment, "locker1").selected_submission
    target = milestone(environment, "m4")
    inherit_snapshot(target, source)
    form = workflows.build_form(target)
    assert not form.initial.get("start_date")
    assert not form.initial.get("end_date")
    assert not form.initial.get("tentative_demo_date")
    assert not form.initial.get("wasa_agency")
    for key in ("functional_certificate", "functional_report", "undertaking_form"):
        assert not form.existing_files.get(key)
    target.refresh_from_db()
    assert target.selected_submission_id == source.pk
    assert target.application.form_uses.get().selected_submission_id == source.pk

    item, form, saved = workflows.save_review_form(
        target,
        environment["applicant"],
        data=sandbox_dates(),
        submit=True,
        expected_revision=source.pk,
    )
    assert not saved
    for key in ("functional_certificate", "functional_report", "undertaking_form"):
        assert key in form.errors
    assert item.selected_submission_id == source.pk
    target.refresh_from_db()
    assert target.status == ReviewItem.Status.DRAFT
    assert target.selected_submission_id == source.pk


def test_same_track_inherited_pin_keeps_files_but_requires_new_dates(environment):
    source = submit(environment).selected_submission
    target = milestone(environment, "m2")
    inherit_snapshot(target, source)
    form = workflows.build_form(target)
    assert not form.initial.get("start_date")
    assert not form.initial.get("end_date")
    assert form.initial["tentative_demo_date"] == source.data["tentative_demo_date"]
    assert form.existing_files["functional_report"]
    target.refresh_from_db()
    assert target.selected_submission_id == source.pk

    item, form, saved = workflows.save_review_form(
        target,
        environment["applicant"],
        expected_revision=source.pk,
        data={**source.data, **sandbox_dates()},
        submit=True,
    )
    assert saved, form.errors
    for key, value in sandbox_dates().items():
        assert item.selected_submission.data[key] == value
    assert item.selected_submission.attachments.count() == 4


def test_cross_track_inherited_pin_still_offers_approved_product_wasa(environment):
    source = approve(environment, "locker1").selected_submission
    target = milestone(environment, "m4")
    inherit_snapshot(target, source)
    form = workflows.build_form(target)
    assert form.initial["use_product_wasa"]
    assert form.wasa_source.pk == source.pk
    assert form.existing_files["wasa_certificate"]
    assert not form.existing_files.get("functional_report")
    assert not form.initial.get("tentative_demo_date")


def test_current_milestone_draft_keeps_its_own_testing_dates_and_files(environment):
    current, form, saved = workflows.save_review_form(
        milestone(environment),
        environment["applicant"],
        data=evidence_data(),
        files=files(),
    )
    assert saved, form.errors
    form = workflows.build_form(current)
    assert form.initial["start_date"] == evidence_data()["start_date"]
    assert form.initial["end_date"] == evidence_data()["end_date"]
    assert form.existing_files["functional_report"]


@pytest.mark.parametrize("actor", ["outsider", "reviewer"])
def test_integrator_membership_is_required_for_group_submission(environment, actor):
    target = milestone(environment)
    with pytest.raises(PermissionDenied):
        workflows.save_review_forms(
            target,
            environment[actor],
            data=evidence_data(),
            files=files(),
            expected_revision="",
        )
    assert_fresh(target)
