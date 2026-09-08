"""The only write boundary for the portal's rows.

Views collect input and call these; nothing else moves a status. Every
function checks who is acting, records the audit diff, appends the review
history the design doc asks for, and sends the matching notification.
"""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ohc_experience.organisations.models import MANAGER_ROLES
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.users.permissions import is_ohc_team

from . import notifications
from . import tracks
from .audit import record_audit
from .audit import snapshot
from .callback import probe_callback
from .models import ComplianceRecord
from .models import Credential
from .models import Product
from .models import ReviewHistory
from .models import ReviewItem
from .models import ReviewQuery
from .references import next_review_reference
from .references import next_sandbox_id

# Roles that may register products, file exit requests and answer queries.
WRITER_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.DEVELOPER})
SECRET_ALPHABET = string.ascii_letters + string.digits
SECRET_LENGTH = 40
PRODUCT_FIELDS = ("name", "description", "category", "solution_type")


# ── who may act ────────────────────────────────────────────────────────────


def require_member(organisation, user):
    membership = organisation.memberships.filter(user=user).first()
    if membership is None:
        msg = _("You are not a member of this organisation.")
        raise PermissionDenied(msg)
    return membership


def require_writer(organisation, user):
    membership = require_member(organisation, user)
    if membership.role not in WRITER_ROLES:
        msg = _("Support members can read this but not change it.")
        raise PermissionDenied(msg)
    return membership


def require_manager(organisation, user):
    membership = require_member(organisation, user)
    if membership.role not in MANAGER_ROLES:
        msg = _("Only the owner and admins can change the organisation.")
        raise PermissionDenied(msg)
    return membership


def require_reviewer(user) -> None:
    if not is_ohc_team(user) and not getattr(user, "is_superuser", False):
        msg = _("Only NHA reviewers can do this.")
        raise PermissionDenied(msg)


def require_admin(user) -> None:
    if not getattr(user, "is_superuser", False):
        msg = _("Only an administrator can assign reviewers.")
        raise PermissionDenied(msg)


# ── history ────────────────────────────────────────────────────────────────


def _history(  # noqa: PLR0913
    item,
    *,
    actor,
    kind,
    title,
    description="",
    payload=None,
):
    return ReviewHistory.objects.create(
        item=item,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        kind=kind,
        title=str(title),
        description=str(description),
        payload=payload or {},
    )


def _open_item(
    *,
    item_type,
    organisation,
    user,
    product=None,
    compliance=None,
):
    """Create the review item for a subject, or send an existing one back in."""
    existing = ReviewItem.objects.filter(item_type=item_type, organisation=organisation)
    if compliance is not None:
        existing = existing.filter(compliance=compliance)
    elif product is not None:
        existing = existing.filter(product=product)
    item = existing.select_for_update().first()
    now = timezone.now()
    if item is None:
        item = ReviewItem.objects.create(
            reference=next_review_reference(),
            item_type=item_type,
            organisation=organisation,
            product=product,
            compliance=compliance,
            status=ReviewItem.Status.NEW,
            submitted_on=now,
        )
        _history(
            item,
            actor=user,
            kind=ReviewHistory.Kind.SUBMITTED,
            title=_("Submitted for review"),
        )
        record_audit(actor=user, instance=item, action="create")
        notifications.notify_item_submitted(item)
        return item
    if item.is_open:
        msg = _("This is already with the review team.")
        raise ValidationError(msg)
    before = snapshot(item)
    item.status = ReviewItem.Status.IN_REVIEW
    item.resubmission_count += 1
    item.submitted_on = now
    item.decided_on = None
    item.decided_by = None
    item.decision_note = ""
    item.save()
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.RESUBMITTED,
        title=_("Resubmitted"),
        payload={"resubmission": item.resubmission_count},
    )
    record_audit(actor=user, instance=item, action="resubmit", before=before)
    notifications.notify_item_submitted(item)
    return item


# ── organisation ───────────────────────────────────────────────────────────


@transaction.atomic
def save_organisation_details(*, organisation, user, form):
    """Save the details form. Reviewers with an open item see it in history."""
    require_manager(organisation, user)
    before = snapshot(organisation)
    organisation = form.save()
    record_audit(actor=user, instance=organisation, action="update", before=before)
    item = organisation.verification_review_item
    if item is not None and item.is_open:
        _history(
            item,
            actor=user,
            kind=ReviewHistory.Kind.NOTE,
            title=_("Organisation details updated"),
        )
    return organisation


@transaction.atomic
def submit_organisation_for_verification(*, organisation, user) -> ReviewItem:
    require_manager(organisation, user)
    organisation = Organisation.objects.select_for_update().get(pk=organisation.pk)
    if organisation.is_verified:
        msg = _("This organisation is already verified.")
        raise ValidationError(msg)
    missing = organisation.missing_details
    if missing:
        msg = _("Complete these details first: %(fields)s.") % {
            "fields": ", ".join(missing),
        }
        raise ValidationError(msg)
    before = snapshot(organisation)
    now = timezone.now()
    organisation.verification_status = Organisation.VerificationStatus.PENDING
    organisation.verification_reason = ""
    organisation.verification_submitted_at = now
    if organisation.onboarded_at is None:
        organisation.onboarded_at = now
    organisation.save()
    item = _open_item(
        item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
        organisation=organisation,
        user=user,
    )
    record_audit(
        actor=user,
        instance=organisation,
        action="submit_verification",
        before=before,
    )
    return item


# ── queries (integrator side) ──────────────────────────────────────────────


@transaction.atomic
def reply_to_query(*, query, user, reply: str) -> ReviewQuery:
    # Lock the query row only: the item's compliance join is nullable, and
    # Postgres refuses FOR UPDATE across the nullable side of an outer join.
    query = (
        ReviewQuery.objects.select_for_update(of=("self",))
        .select_related("item")
        .get(pk=query.pk)
    )
    item = query.item
    require_writer(item.organisation, user)
    if not query.is_open:
        msg = _("This query has already been answered.")
        raise ValidationError(msg)
    query.status = ReviewQuery.Status.ANSWERED
    query.reply = reply
    query.replied_by = user
    query.replied_at = timezone.now()
    query.save()
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.QUERY_ANSWERED,
        title=_("Query answered"),
        description=reply,
        payload={"query_id": query.pk},
    )
    if not item.queries.filter(status=ReviewQuery.Status.OPEN).exists():
        _resume_after_queries(item, user)
    notifications.notify_query_answered(query)
    return query


def _resume_after_queries(item, user) -> None:
    """Every query answered: the item and its record go back under review."""
    if item.status == ReviewItem.Status.QUERY_RAISED:
        before = snapshot(item)
        item.status = ReviewItem.Status.IN_REVIEW
        item.save(update_fields=["status", "updated_at"])
        record_audit(
            actor=user,
            instance=item,
            action="queries_answered",
            before=before,
        )
    record = item.compliance
    if record is not None and record.status == ComplianceRecord.Status.QUERY_RAISED:
        before = snapshot(record)
        record.status = ComplianceRecord.Status.UNDER_REVIEW
        record.save(update_fields=["status", "updated_at"])
        record_audit(
            actor=user,
            instance=record,
            action="queries_answered",
            before=before,
        )


# ── products ───────────────────────────────────────────────────────────────


def _ordered_tracks(codes) -> list[str]:
    wanted = set(codes)
    return [track.code for track in tracks.TRACKS if track.code in wanted]


def sync_milestone_locks(product) -> None:
    """Open the first unapproved milestone on every applied track.

    Walks each track in milestone order: a record stays locked until every
    record before it on that track is approved. The shared HI-CM M1 record is
    reached through PHR too, which is how PHR1 waits on it.
    """
    record_map = product.record_map()
    for track in tracks.TRACKS:
        blocked = False
        for milestone in track.milestones:
            record = record_map.get(milestone.canonical_key)
            if record is None:
                continue
            if not blocked and record.status == ComplianceRecord.Status.LOCKED:
                record.status = ComplianceRecord.Status.OPEN
                record.save(update_fields=["status", "updated_at"])
            if record.status != ComplianceRecord.Status.APPROVED:
                blocked = True


def _sync_records(product, canonical: set[str], user) -> tuple[list[str], list[str]]:
    """Create records for new milestones and drop removable ones.

    Returns the canonical keys added and removed.
    """
    existing = product.record_map()
    added, removed = [], []
    for key in sorted(canonical - set(existing)):
        track_code, code = tracks.parse_key(key)
        record = ComplianceRecord.objects.create(
            product=product,
            track_code=track_code,
            milestone_code=code,
            status=ComplianceRecord.Status.LOCKED,
        )
        record_audit(actor=user, instance=record, action="create")
        added.append(key)
    for key in sorted(set(existing) - canonical):
        record = existing[key]
        if record.is_approved or record.is_under_review:
            msg = _(
                "%(label)s cannot be removed once it is approved or under review.",
            ) % {"label": record.label}
            raise ValidationError(msg)
        record_audit(actor=user, instance=record, action="delete")
        record.delete()
        removed.append(key)
    sync_milestone_locks(product)
    return added, removed


@transaction.atomic
def register_product(*, organisation, user, data: dict):
    require_writer(organisation, user)
    canonical, applied = tracks.validate_selection(data["milestones"])
    product = Product.objects.create(
        organisation=organisation,
        sandbox_id=next_sandbox_id(),
        applied_tracks=_ordered_tracks(applied),
        applied_milestones=sorted(canonical),
        created_by=user,
        **{name: data[name] for name in PRODUCT_FIELDS},
    )
    record_audit(actor=user, instance=product, action="create")
    _sync_records(product, canonical, user)
    _open_item(
        item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
        organisation=organisation,
        user=user,
        product=product,
    )
    issue_credentials(product=product, actor=user)
    return product


@transaction.atomic
def update_product(*, product, user, data: dict):
    require_writer(product.organisation, user)
    product = Product.objects.select_for_update().get(pk=product.pk)
    canonical, applied = tracks.validate_selection(data["milestones"])
    before = snapshot(product)
    for name in PRODUCT_FIELDS:
        setattr(product, name, data[name])
    product.applied_tracks = _ordered_tracks(applied)
    product.applied_milestones = sorted(canonical)
    if product.is_sent_back:
        product.registration_status = Product.RegistrationStatus.PENDING
        product.sent_back_reason = ""
    product.save()
    added, removed = _sync_records(product, canonical, user)
    record_audit(actor=user, instance=product, action="update", before=before)
    item = product.review_items.filter(
        item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
    ).first()
    if before["registration_status"] == Product.RegistrationStatus.SENT_BACK:
        _open_item(
            item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
            organisation=product.organisation,
            user=user,
            product=product,
        )
    if added or removed:
        if item is not None:
            _history(
                item,
                actor=user,
                kind=ReviewHistory.Kind.MILESTONES_CHANGED,
                title=_("Milestones changed"),
                description=_("Added: %(added)s. Removed: %(removed)s.")
                % {
                    "added": ", ".join(added) or "—",
                    "removed": ", ".join(removed) or "—",
                },
                payload={"added": added, "removed": removed},
            )
        if added:
            notifications.notify_milestones_added(product, added)
    return product


# ── credentials ────────────────────────────────────────────────────────────


def generate_secret() -> str:
    return "".join(secrets.choice(SECRET_ALPHABET) for _ in range(SECRET_LENGTH))


def _client_id(product) -> str:
    stem = product.sandbox_id.replace("-", "_")
    return f"{stem}_{secrets.token_hex(3).upper()}"


def _rotation_due():
    days = settings.ABDM_CREDENTIAL_ROTATION_DAYS
    return timezone.localdate() + timedelta(days=days)


@transaction.atomic
def issue_credentials(*, product, actor=None):
    """Issue sandbox credentials once the organisation is verified.

    Safe to call repeatedly: a product with active credentials keeps them, an
    unverified organisation gets nothing, and a revoked credential is reissued
    with a new client id and secret.
    """
    if not product.organisation.is_verified:
        return None
    credential = getattr(product, "credential", None)
    if credential is not None and credential.is_active:
        return credential
    now = timezone.now()
    secret = generate_secret()
    if credential is None:
        credential = Credential(
            product=product,
            client_id=_client_id(product),
            gateway_base_url=settings.ABDM_SANDBOX_GATEWAY_URL,
            issued_on=now,
            rotation_due=_rotation_due(),
        )
        credential.set_secret(secret)
        credential.save()
        record_audit(actor=actor, instance=credential, action="issue")
    else:
        before = snapshot(credential)
        credential.client_id = _client_id(product)
        credential.set_secret(secret)
        credential.issued_on = now
        credential.rotation_due = _rotation_due()
        credential.rotated_on = None
        credential.revoked_on = None
        credential.save()
        record_audit(actor=actor, instance=credential, action="reissue", before=before)
    notifications.notify_credentials_issued(product)
    return credential


def _active_credential(product):
    credential = getattr(product, "credential", None)
    if credential is None or not credential.is_active:
        msg = _("This product has no active sandbox credentials.")
        raise ValidationError(msg)
    return credential


@transaction.atomic
def rotate_credentials(*, product, user):
    """Replace the client secret. The client id stays; rotation_due moves on."""
    require_writer(product.organisation, user)
    credential = Credential.objects.select_for_update().get(
        pk=_active_credential(product).pk,
    )
    before = snapshot(credential)
    credential.set_secret(generate_secret())
    credential.rotated_on = timezone.now()
    credential.rotation_due = _rotation_due()
    credential.save()
    record_audit(actor=user, instance=credential, action="rotate", before=before)
    return credential


@transaction.atomic
def revoke_credentials(*, product, user) -> None:
    require_writer(product.organisation, user)
    credential = Credential.objects.select_for_update().get(
        pk=_active_credential(product).pk,
    )
    before = snapshot(credential)
    credential.revoked_on = timezone.now()
    credential.save(update_fields=["revoked_on", "updated_at"])
    record_audit(actor=user, instance=credential, action="revoke", before=before)


@transaction.atomic
def request_credentials(*, product, user):
    """Reissue after a revoke. The organisation must still be verified."""
    require_writer(product.organisation, user)
    if not product.organisation.is_verified:
        msg = _("Credentials are issued once the organisation is verified.")
        raise ValidationError(msg)
    credential = getattr(product, "credential", None)
    if credential is not None and credential.is_active:
        return credential
    return issue_credentials(product=product, actor=user)


@transaction.atomic
def update_callback_urls(*, product, user, callback_url: str, bridge_url: str):
    require_writer(product.organisation, user)
    credential = Credential.objects.select_for_update().get(
        pk=_active_credential(product).pk,
    )
    before = snapshot(credential)
    if credential.callback_url != callback_url:
        # A new URL starts with a clean slate: yesterday's failures were the
        # old endpoint's.
        credential.callback_status_code = None
        credential.callback_latency_ms = None
        credential.callback_checked_at = None
        credential.callback_error = ""
        credential.callback_failure_streak = 0
    credential.callback_url = callback_url
    credential.bridge_url = bridge_url
    credential.save()
    record_audit(actor=user, instance=credential, action="update_urls", before=before)
    return credential


@transaction.atomic
def check_callback(*, product, actor=None, probe=None):
    """Probe the callback URL, store the result, and mail on the Nth failure in a row.

    `probe` is injectable (and the module-level probe patchable) so tests never
    open a socket.
    """
    credential = Credential.objects.select_for_update().get(
        pk=_active_credential(product).pk,
    )
    if not credential.callback_url:
        msg = _("Set a callback URL before testing it.")
        raise ValidationError(msg)
    result = (probe or probe_callback)(credential.callback_url)
    before = snapshot(credential)
    credential.callback_status_code = result.status_code
    credential.callback_latency_ms = result.latency_ms
    credential.callback_error = result.error
    credential.callback_checked_at = timezone.now()
    credential.callback_failure_streak = (
        0 if result.ok else credential.callback_failure_streak + 1
    )
    credential.save()
    record_audit(
        actor=actor,
        instance=credential,
        action="callback_check",
        before=before,
    )
    if credential.callback_failure_streak == settings.ABDM_CALLBACK_FAILURE_ALERT:
        notifications.notify_callback_failing(product)
    return credential


def _reveal_cache_key(user) -> str:
    return f"abdm:reveal:{user.pk}"


def reveal_secret(*, product, user) -> str:
    """Hand back the client secret, audited and rate-limited per user."""
    require_writer(product.organisation, user)
    credential = _active_credential(product)
    key = _reveal_cache_key(user)
    limit = settings.ABDM_SECRET_REVEAL_LIMIT
    window = settings.ABDM_SECRET_REVEAL_WINDOW
    count = cache.get(key, 0)
    if count >= limit:
        msg = _(
            "The secret was revealed %(limit)s times in the last %(minutes)s minutes. "
            "Try again later.",
        ) % {"limit": limit, "minutes": window // 60}
        raise PermissionDenied(msg)
    if count == 0:
        cache.set(key, 1, timeout=window)
    else:
        cache.incr(key)
    record_audit(
        actor=user,
        instance=credential,
        action="reveal_secret",
        before=snapshot(credential),
    )
    return credential.secret


# ── exit requests ──────────────────────────────────────────────────────────


def _locked_record(record):
    return (
        ComplianceRecord.objects.select_for_update()
        .select_related(
            "product__organisation",
        )
        .get(pk=record.pk)
    )


@transaction.atomic
def save_exit_draft(*, record, user, form):
    """Save the form as a draft. Open becomes in progress; nothing is submitted."""
    require_writer(record.product.organisation, user)
    locked = _locked_record(record)
    if not locked.is_editable:
        msg = _("This exit request cannot be edited while it is %(status)s.") % {
            "status": locked.get_status_display().lower(),
        }
        raise ValidationError(msg)
    before = snapshot(locked)
    # The form was validated against its own instance; save that one, with
    # the status read off the locked row.
    record = form.save(commit=False)
    record.status = locked.status
    if record.status == ComplianceRecord.Status.OPEN:
        record.status = ComplianceRecord.Status.IN_PROGRESS
    record.save()
    record_audit(actor=user, instance=record, action="save_draft", before=before)
    return record


@transaction.atomic
def request_exit(*, record, user) -> ReviewItem:
    """Submit the exit request. Every field and both files must be present."""
    require_writer(record.product.organisation, user)
    record = _locked_record(record)
    if not record.is_editable:
        msg = _("This exit request is already %(status)s.") % {
            "status": record.get_status_display().lower(),
        }
        raise ValidationError(msg)
    missing = record.missing_fields
    if missing:
        msg = _("Complete these before requesting exit: %(fields)s.") % {
            "fields": ", ".join(missing),
        }
        raise ValidationError(msg)
    before = snapshot(record)
    if record.submitted_on is not None:
        record.resubmission_count += 1
    record.status = ComplianceRecord.Status.UNDER_REVIEW
    record.submitted_on = timezone.now()
    record.save()
    item = _open_item(
        item_type=ReviewItem.Type.EXIT_REQUEST,
        organisation=record.product.organisation,
        user=user,
        product=record.product,
        compliance=record,
    )
    record_audit(actor=user, instance=record, action="request_exit", before=before)
    return item


@transaction.atomic
def withdraw_exit(*, record, user) -> None:
    """Take the request back: the form unlocks, the item leaves the queue."""
    require_writer(record.product.organisation, user)
    record = _locked_record(record)
    if not record.is_under_review:
        msg = _("Only a request under review can be withdrawn.")
        raise ValidationError(msg)
    before = snapshot(record)
    record.status = ComplianceRecord.Status.IN_PROGRESS
    record.save(update_fields=["status", "updated_at"])
    record_audit(actor=user, instance=record, action="withdraw", before=before)
    item = record.review_item
    if item is not None:
        item_before = snapshot(item)
        item.status = ReviewItem.Status.WITHDRAWN
        item.save(update_fields=["status", "updated_at"])
        now = timezone.now()
        item.queries.filter(status=ReviewQuery.Status.OPEN).update(
            status=ReviewQuery.Status.RESOLVED,
            resolved_at=now,
            resolved_by=user,
        )
        _history(
            item,
            actor=user,
            kind=ReviewHistory.Kind.WITHDRAWN,
            title=_("Request withdrawn"),
            description=_("Open queries were closed with the withdrawal."),
        )
        record_audit(actor=user, instance=item, action="withdraw", before=item_before)


# ── review decisions ───────────────────────────────────────────────────────


def _locked_item(item):
    # Lock the item row only: product and compliance are nullable joins, and
    # Postgres refuses FOR UPDATE across the nullable side of an outer join.
    return (
        ReviewItem.objects.select_for_update(of=("self",))
        .select_related("organisation", "product", "compliance")
        .get(pk=item.pk)
    )


def _require_open(item) -> None:
    if not item.is_open:
        msg = _("This item is already %(status)s.") % {
            "status": item.get_status_display().lower(),
        }
        raise ValidationError(msg)


@transaction.atomic
def start_review(*, item, user) -> None:
    require_reviewer(user)
    item = _locked_item(item)
    if item.status != ReviewItem.Status.NEW:
        msg = _("The review has already started.")
        raise ValidationError(msg)
    before = snapshot(item)
    item.status = ReviewItem.Status.IN_REVIEW
    item.save(update_fields=["status", "updated_at"])
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.STARTED,
        title=_("Review started"),
    )
    record_audit(actor=user, instance=item, action="start_review", before=before)


@transaction.atomic
def assign_reviewer(*, item, actor, assignee) -> None:
    """Manual assignment by an administrator (design doc, answer 3)."""
    require_admin(actor)
    if assignee is not None and not is_ohc_team(assignee):
        msg = _("Only NHA reviewers can be assigned.")
        raise ValidationError(msg)
    item = _locked_item(item)
    before = snapshot(item)
    item.assignee = assignee
    item.assigned_by = actor if assignee is not None else None
    item.save(update_fields=["assignee", "assigned_by", "updated_at"])
    _history(
        item,
        actor=actor,
        kind=ReviewHistory.Kind.ASSIGNED,
        title=_("Assigned to %(name)s") % {"name": assignee.display_name}
        if assignee
        else _("Unassigned"),
        payload={"assignee_id": assignee.pk if assignee else None},
    )
    record_audit(actor=actor, instance=item, action="assign", before=before)


@transaction.atomic
def raise_query(*, item, user, field_key: str, field_label: str, question: str):
    """Pause the item on a question against one field, or the whole form."""
    require_reviewer(user)
    item = _locked_item(item)
    _require_open(item)
    query = ReviewQuery.objects.create(
        item=item,
        field_key=field_key or "form",
        field_label=field_label,
        question=question,
        raised_by=user,
    )
    before = snapshot(item)
    item.status = ReviewItem.Status.QUERY_RAISED
    item.save(update_fields=["status", "updated_at"])
    record_audit(actor=user, instance=item, action="raise_query", before=before)
    record = item.compliance
    if record is not None and record.status == ComplianceRecord.Status.UNDER_REVIEW:
        record_before = snapshot(record)
        record.status = ComplianceRecord.Status.QUERY_RAISED
        record.save(update_fields=["status", "updated_at"])
        record_audit(
            actor=user,
            instance=record,
            action="raise_query",
            before=record_before,
        )
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.QUERY_RAISED,
        title=_("Query raised against %(field)s") % {"field": query.against_label},
        description=question,
        payload={"query_id": query.pk, "field_key": query.field_key},
    )
    notifications.notify_query_raised(query)
    return query


@transaction.atomic
def resolve_query(*, query, user) -> None:
    require_reviewer(user)
    query = (
        ReviewQuery.objects.select_for_update(of=("self",))
        .select_related("item")
        .get(pk=query.pk)
    )
    if query.is_resolved:
        return
    query.status = ReviewQuery.Status.RESOLVED
    query.resolved_at = timezone.now()
    query.resolved_by = user
    query.save()
    item = query.item
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.QUERY_RESOLVED,
        title=_("Query resolved"),
        payload={"query_id": query.pk},
    )
    if not item.queries.filter(status=ReviewQuery.Status.OPEN).exists():
        _resume_after_queries(item, user)


def _apply_approval(item, user, approved_on, note: str) -> None:
    """What approval means for the subject: verified / registered / approved."""
    if item.is_organisation_verification:
        organisation = item.organisation
        before = snapshot(organisation)
        organisation.set_verification(
            Organisation.VerificationStatus.VERIFIED,
            actor=user,
        )
        record_audit(actor=user, instance=organisation, action="verify", before=before)
        for product in organisation.products.all():
            issue_credentials(product=product, actor=user)
    elif item.is_product_registration:
        product = item.product
        before = snapshot(product)
        product.registration_status = Product.RegistrationStatus.REGISTERED
        product.registered_on = approved_on
        product.registered_by = user
        product.sent_back_reason = ""
        product.save()
        record_audit(actor=user, instance=product, action="register", before=before)
    else:
        record = item.compliance
        before = snapshot(record)
        record.status = ComplianceRecord.Status.APPROVED
        record.approved_on = approved_on
        record.approved_by = user
        record.decision_note = note
        record.save()
        record_audit(actor=user, instance=record, action="approve", before=before)
        sync_milestone_locks(record.product)


def _apply_send_back(item, user, reason: str) -> None:
    if item.is_organisation_verification:
        organisation = item.organisation
        before = snapshot(organisation)
        organisation.set_verification(
            Organisation.VerificationStatus.SENT_BACK,
            actor=user,
            reason=reason,
        )
        record_audit(
            actor=user,
            instance=organisation,
            action="send_back",
            before=before,
        )
    elif item.is_product_registration:
        product = item.product
        before = snapshot(product)
        product.registration_status = Product.RegistrationStatus.SENT_BACK
        product.sent_back_reason = reason
        product.save()
        record_audit(actor=user, instance=product, action="send_back", before=before)
    else:
        record = item.compliance
        before = snapshot(record)
        record.status = ComplianceRecord.Status.IN_PROGRESS
        record.sent_back_on = timezone.now()
        record.sent_back_by = user
        record.sent_back_reason = reason
        record.save()
        record_audit(actor=user, instance=record, action="send_back", before=before)


def _close_open_queries(item, user) -> None:
    item.queries.filter(status=ReviewQuery.Status.OPEN).update(
        status=ReviewQuery.Status.RESOLVED,
        resolved_at=timezone.now(),
        resolved_by=user,
    )


@transaction.atomic
def approve(*, item, user, approved_on=None, note: str = "") -> None:
    require_reviewer(user)
    item = _locked_item(item)
    _require_open(item)
    if item.queries.filter(status=ReviewQuery.Status.OPEN).exists():
        msg = _("Resolve or wait for every open query before approving.")
        raise ValidationError(msg)
    approved_on = approved_on or timezone.localdate()
    before = snapshot(item)
    item.status = ReviewItem.Status.APPROVED
    item.decided_on = approved_on
    item.decided_by = user
    item.decision_note = note
    item.save()
    record_audit(actor=user, instance=item, action="approve", before=before)
    _apply_approval(item, user, approved_on, note)
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.APPROVED,
        title=_("Approved"),
        description=note,
        payload={"approved_on": approved_on.isoformat()},
    )
    notifications.notify_item_approved(item)


@transaction.atomic
def send_back(*, item, user, reason: str) -> None:
    require_reviewer(user)
    if not reason.strip():
        msg = _("Give the integrator a reason.")
        raise ValidationError(msg)
    item = _locked_item(item)
    _require_open(item)
    before = snapshot(item)
    item.status = ReviewItem.Status.SENT_BACK
    item.decided_on = timezone.localdate()
    item.decided_by = user
    item.decision_note = reason
    item.save()
    _close_open_queries(item, user)
    record_audit(actor=user, instance=item, action="send_back", before=before)
    _apply_send_back(item, user, reason)
    _history(
        item,
        actor=user,
        kind=ReviewHistory.Kind.SENT_BACK,
        title=_("Sent back to the integrator"),
        description=reason,
    )
    notifications.notify_item_sent_back(item, reason)
