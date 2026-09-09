"""Product WASA projections backed by immutable, reviewed form submissions."""

from datetime import date

from django.utils import timezone

from ohc_experience.experiences.definitions import OutcomeDefinition
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import ProductOutcomeStatus
from ohc_experience.experiences.models import ReviewItem

WASA_OUTCOME = "wasa_approval"
WASA_APPLICATION = "abdm_wasa_review"
WASA_FIELDS = ("wasa_agency", "wasa_date", "wasa_valid_until")


def as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value) if value else None
    except TypeError, ValueError:
        return None


def approved_wasa_outcomes(product):
    """Latest audit wins even when an older certificate is approved later."""
    outcomes = product.outcomes.filter(outcome_type=WASA_OUTCOME)
    return sorted(
        outcomes.select_related("source_application"),
        key=lambda outcome: (
            as_date(outcome.data.get("wasa_date")) or date.min,
            outcome.issued_at,
            outcome.pk,
        ),
        reverse=True,
    )


def current_wasa(product):
    return next(iter(approved_wasa_outcomes(product)), None)


def approved_wasa_submission(product, submission_id, *, require_valid=True):
    """Resolve a posted pin through approvals for this product, never raw IDs."""
    if not product or not submission_id:
        return None
    try:
        submission_id = int(submission_id)
    except TypeError, ValueError:
        return None
    approval = next(
        (
            outcome
            for outcome in approved_wasa_outcomes(product)
            if outcome.data.get("submission_id") == submission_id
        ),
        None,
    )
    if not approval or (
        require_valid
        and (
            approval.status != ProductOutcomeStatus.ACTIVE
            or not approval.valid_until
            or approval.is_expired
        )
    ):
        return None
    submission = FormSubmission.objects.filter(
        pk=submission_id,
        form__product=product,
        origin_application=approval.source_application,
        status="completed",
    ).first()
    if not submission:
        return None
    expiry = as_date(submission.data.get("wasa_valid_until"))
    if require_valid and (
        not expiry
        or expiry != approval.valid_until
        or expiry < timezone.localdate()
        or not submission.attachments.filter(
            field_key="wasa_certificate",
            is_current=True,
        ).exists()
    ):
        return None
    return submission


def preferred_wasa_submission(item):
    saved = item.selected_submission
    if (
        saved
        and saved.origin_application_id == item.application_id
        and saved.data.get("use_product_wasa")
    ):
        # Retain the exact reviewed source selected by this milestone, including
        # when a newer product certificate is approved while the draft is open.
        return approved_wasa_submission(
            item.product,
            saved.data.get("wasa_source_submission"),
            require_valid=False,
        )
    current = current_wasa(item.product)
    return approved_wasa_submission(
        item.product,
        current.data.get("submission_id") if current else None,
    )


def certificate_context(submission, *, valid_until=None):
    data = submission.data if submission else {}
    return {
        "source_submission": submission,
        "agency": data.get("wasa_agency", ""),
        "audit_date": as_date(data.get("wasa_date")),
        "valid_until": valid_until or as_date(data.get("wasa_valid_until")),
        "certificate": (
            submission.attachments.filter(
                field_key="wasa_certificate",
                is_current=True,
            ).first()
            if submission
            else None
        ),
    }


def _legacy_approved_submission(product):
    """Show historical evidence without inventing certification validity."""
    item = (
        product.review_items.filter(
            form__form_key="sandbox_exit_evidence",
            status=ReviewItem.Status.APPROVED,
            selected_submission__isnull=False,
        )
        .select_related("selected_submission")
        .order_by("-decided_at", "-pk")
        .first()
    )
    return item.selected_submission if item else None


def wasa_context(product):
    history = approved_wasa_outcomes(product)
    current = history[0] if history else None
    source = (
        approved_wasa_submission(
            product,
            current.data.get("submission_id"),
            require_valid=False,
        )
        if current
        else _legacy_approved_submission(product)
    )
    context = certificate_context(
        source,
        valid_until=current.valid_until if current else None,
    )
    expiry = context["valid_until"]
    if not current and not source:
        status, label, tone = "missing", "Not submitted", "neutral"
    elif current and current.status == ProductOutcomeStatus.REVOKED:
        status, label, tone = "revoked", "Revoked", "warning"
    elif not current or not expiry or not source:
        status, label, tone = (
            "needs_verification",
            "Expiry needs verification",
            "warning",
        )
    elif current.is_expired or current.status == ProductOutcomeStatus.EXPIRED:
        status, label, tone = "expired", "Expired", "warning"
    elif (expiry - timezone.localdate()).days <= 30:  # noqa: PLR2004
        status, label, tone = "expiring", "Expiring soon", "warning"
    else:
        status, label, tone = "valid", "Valid", "success"
    review = (
        product.review_items.filter(application__application_type=WASA_APPLICATION)
        .exclude(status=ReviewItem.Status.APPROVED)
        .select_related("application", "selected_submission")
        .order_by("-created_at", "-pk")
        .first()
    )
    if status == "missing" and review:
        label = "No approved certificate"
    return context | {
        "status": status,
        "status_label": label,
        "tone": tone,
        "current": current,
        "review": review,
        "pending_review": review if review and review.pending else None,
        "history": history,
        "can_start": review is None,
        "action_label": "Renew WASA" if source else "Submit WASA",
    }


def wasa_approval_block_reason(item):
    submission = item.selected_submission
    if not submission:
        return "Submit a WASA certificate before approval."
    expiry = as_date(submission.data.get("wasa_valid_until"))
    if not expiry:
        return "The WASA expiry date needs verification before approval."
    if expiry < timezone.localdate():
        return "The WASA certificate has expired. Request a renewed certificate."
    if submission.data.get("use_product_wasa") and not approved_wasa_submission(
        item.product,
        submission.data.get("wasa_source_submission"),
    ):
        return (
            "The selected product WASA is no longer valid. "
            "Request a renewed certificate."
        )
    return ""


def wasa_approval_outcomes(item):
    submission = item.selected_submission
    if submission.data.get("use_product_wasa"):
        return ()
    return (
        OutcomeDefinition(
            key=WASA_OUTCOME,
            name="WASA certification approved",
            valid_until=as_date(submission.data.get("wasa_valid_until")),
            field_schema=[
                {"key": "wasa_agency", "label": "WASA audit agency"},
                {"key": "wasa_date", "label": "WASA audit date"},
                {"key": "wasa_valid_until", "label": "Valid until"},
            ],
            data={
                "submission_id": submission.pk,
                **{key: submission.data.get(key) for key in WASA_FIELDS},
            },
        ),
    )
