from __future__ import annotations

import secrets
import string
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import permission_keys
from .definitions import ActionResult
from .definitions import ExperienceContext
from .definitions import FormActionResult
from .fields import MultipleFileField
from .models import ApplicationAccess
from .models import ApplicationAttachment
from .models import ApplicationEvent
from .models import ApplicationFormSubmission
from .models import ApplicationInstance
from .models import ApplicationQueryMessage
from .models import ApplicationQueryThread
from .models import EventKind
from .models import QueryMessageKind
from .models import QueryStatus
from .models import SubmissionStatus
from .permissions import get_effective_access
from .registry import registry

REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def application_context(application, user) -> ExperienceContext:
    access = get_effective_access(application, user)
    submissions = {}
    history: dict[str, list[ApplicationFormSubmission]] = {}
    for submission in application.submissions.all():
        history.setdefault(submission.form_key, []).append(submission)
        if submission.is_current:
            submissions[submission.form_key] = submission
    return ExperienceContext(
        application=application,
        user=user,
        permissions=access.permissions,
        submissions=submissions,
        submission_history={
            form_key: tuple(items) for form_key, items in history.items()
        },
    )


def _make_reference(prefix: str) -> str:
    for _attempt in range(10):
        suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        reference = f"{prefix}-{timezone.now():%y}-{suffix}"
        if not ApplicationInstance.objects.filter(reference=reference).exists():
            return reference
    msg = _("Could not allocate a unique application reference. Try again.")
    raise ValidationError(msg)


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


@transaction.atomic
def create_application(*, application_type: str, organisation, user):
    definition = registry.get(application_type)
    if not organisation.memberships.filter(user=user).exists():
        msg = _("Only an organisation member can start its application.")
        raise PermissionDenied(msg)

    application = ApplicationInstance.objects.create(
        reference=_make_reference(definition.reference_prefix),
        application_type=definition.key,
        title=definition.name,
        organisation=organisation,
        created_by=user,
        status=definition.initial_status,
        metadata={"progress_percent": 0, "completed_forms": 0},
    )
    ApplicationAccess.objects.create(
        application=application,
        user=user,
        role_key=definition.owner_role_key,
        granted_by=user,
    )
    ApplicationEvent.objects.create(
        application=application,
        actor=user,
        kind=EventKind.CREATED,
        title=_("Application started"),
        description=_("The application workspace was created."),
        status_after=definition.initial_status,
    )
    recalculate_progress(application, user=user)
    return application


def recalculate_progress(
    application: ApplicationInstance,
    *,
    user=None,
) -> None:
    definition = registry.get(application.application_type)
    context = application_context(application, user or application.created_by)
    progress = definition.calculate_progress(context)
    metadata = dict(application.metadata)
    metadata.update(
        {
            "completed_forms": progress.completed,
            "required_forms": progress.total,
            "progress_percent": progress.percentage,
            "next_form": progress.next_form_key,
        },
    )
    application.metadata = metadata
    application.save(update_fields=["metadata", "updated_at"])


def _submission_payload(form, initial_data):
    stored_data = dict(initial_data)
    uploads: dict[str, list[UploadedFile]] = {}
    multiple_upload_fields: set[str] = set()
    for field_name, value in form.cleaned_data.items():
        form_field = form.fields[field_name]
        if not isinstance(form_field, forms.FileField):
            stored_data[field_name] = _json_value(value)
            continue
        if isinstance(value, UploadedFile):
            uploads[field_name] = [value]
        elif isinstance(value, (list, tuple)):
            uploads[field_name] = [
                upload for upload in value if isinstance(upload, UploadedFile)
            ]
        if isinstance(form_field, MultipleFileField):
            multiple_upload_fields.add(field_name)
        if not uploads.get(field_name):
            uploads.pop(field_name, None)
    return stored_data, uploads, multiple_upload_fields


def _choice_schema(choices) -> list[dict[str, Any]]:
    result = []
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            result.extend(_choice_schema(label))
        else:
            result.append({"value": _json_value(value), "label": str(label)})
    return result


def form_field_schema(form) -> list[dict[str, Any]]:
    return [
        {
            "key": name,
            "label": str(field.label or name.replace("_", " ").title()),
            "type": field.__class__.__name__,
            "required": field.required,
            "choices": _choice_schema(field.choices)
            if getattr(field, "choices", None)
            else [],
        }
        for name, field in form.fields.items()
    ]


def _submission_mode(form_definition, current_submission, requested_mode):
    if current_submission is None:
        return "create"
    if not form_definition.repeatable:
        return "edit"
    mode = requested_mode or "renew"
    if mode not in {"edit", "renew"}:
        raise ValidationError(_("Unknown form submission mode."))
    if mode == "edit" and not form_definition.allow_updates:
        raise PermissionDenied(_("This submission is locked after it is completed."))
    return mode


def _persist_submission(
    *,
    context,
    form_definition,
    submission_values,
    submission_mode,
):
    application = context.application
    form_key = form_definition.key
    current_submission = context.submissions.get(form_key)
    if current_submission:
        current_submission.is_current = False
        current_submission.save(update_fields=["is_current"])
        is_renewal = submission_mode == "renew"
        return ApplicationFormSubmission.objects.create(
            application=application,
            form_key=form_key,
            metadata={} if is_renewal else dict(current_submission.metadata),
            revision=1 if is_renewal else current_submission.revision + 1,
            submission_number=(
                current_submission.submission_number + 1
                if is_renewal
                else current_submission.submission_number
            ),
            **submission_values,
        )
    return ApplicationFormSubmission.objects.create(
        application=application,
        form_key=form_key,
        **submission_values,
    )


def _clone_current_attachments(*, source, destination, form) -> None:
    if source is None:
        return
    file_fields = {
        name
        for name, field in form.fields.items()
        if isinstance(field, forms.FileField)
    }
    for attachment in source.attachments.filter(
        field_key__in=file_fields,
        is_current=True,
    ):
        form_field = form.fields[attachment.field_key]
        removed_ids = getattr(form, "removed_file_ids", {}).get(
            attachment.field_key,
            set(),
        )
        replacing_single_file = (
            not isinstance(form_field, MultipleFileField)
            and form.cleaned_data.get(attachment.field_key)
        )
        if attachment.pk in removed_ids or replacing_single_file:
            continue
        ApplicationAttachment.objects.create(
            submission=destination,
            field_key=attachment.field_key,
            file=attachment.file.name,
            original_name=attachment.original_name,
            content_type=attachment.content_type,
            size=attachment.size,
            uploaded_by=attachment.uploaded_by,
        )


def _store_uploads(
    *,
    submission,
    uploads,
    multiple_upload_fields,
    stored_data,
    user,
) -> None:
    for field_name, field_uploads in uploads.items():
        if field_name not in multiple_upload_fields:
            submission.attachments.filter(
                field_key=field_name,
                is_current=True,
            ).update(is_current=False)
        attachment_data = []
        for upload in field_uploads:
            attachment = ApplicationAttachment.objects.create(
                submission=submission,
                field_key=field_name,
                file=upload,
                original_name=upload.name,
                content_type=getattr(upload, "content_type", ""),
                size=upload.size,
                uploaded_by=user,
            )
            attachment_data.append(
                {
                    "attachment_id": attachment.pk,
                    "name": attachment.original_name,
                    "size": attachment.size,
                },
            )
        stored_data[field_name] = (
            attachment_data
            if field_name in multiple_upload_fields
            else attachment_data[0]
        )


def _synchronize_attachment_data(*, submission, form, stored_data) -> bool:
    has_file_fields = False
    for field_name, form_field in form.fields.items():
        if not isinstance(form_field, forms.FileField):
            continue
        has_file_fields = True
        attachments = list(
            submission.attachments.filter(
                field_key=field_name,
                is_current=True,
            ).order_by("created_at", "pk"),
        )
        attachment_data = [
            {
                "attachment_id": attachment.pk,
                "name": attachment.original_name,
                "size": attachment.size,
            }
            for attachment in attachments
        ]
        if isinstance(form_field, MultipleFileField):
            if attachment_data:
                stored_data[field_name] = attachment_data
            else:
                stored_data.pop(field_name, None)
        elif attachment_data:
            stored_data[field_name] = attachment_data[0]
        else:
            stored_data.pop(field_name, None)
    return has_file_fields


@transaction.atomic
def save_form_submission(
    *,
    application,
    form_key: str,
    form,
    user,
    submission_mode: str | None = None,
):
    application = ApplicationInstance.objects.select_for_update().get(
        pk=application.pk,
    )
    definition = registry.get(application.application_type)
    form_definition = definition.get_form(form_key)
    if form_definition is None:
        raise ValidationError(_("Unknown application form."))
    context = application_context(application, user)
    available, reason = form_definition.availability(context)
    if not available:
        raise PermissionDenied(reason)
    if not form.is_valid():
        raise ValidationError(_("The form must be valid before it is saved."))

    current_submission = context.submissions.get(form_key)
    submission_mode = _submission_mode(
        form_definition,
        current_submission,
        submission_mode,
    )
    stored_data = {}
    if current_submission and submission_mode == "edit":
        stored_data = {
            field_name: current_submission.data[field_name]
            for field_name, field in form.fields.items()
            if isinstance(field, forms.FileField)
            and field_name in current_submission.data
        }
    stored_data, uploads, multiple_upload_fields = _submission_payload(
        form,
        stored_data,
    )
    valid_until = form_definition.get_valid_until(form.cleaned_data)
    submission = _persist_submission(
        context=context,
        form_definition=form_definition,
        submission_values={
            "status": SubmissionStatus.COMPLETED,
            "data": stored_data,
            "field_schema": form_field_schema(form),
            "schema_version": form_definition.schema_version,
            "valid_until": valid_until,
            "submitted_by": user,
        },
        submission_mode=submission_mode,
    )
    if submission_mode == "edit":
        _clone_current_attachments(
            source=current_submission,
            destination=submission,
            form=form,
        )
    _store_uploads(
        submission=submission,
        uploads=uploads,
        multiple_upload_fields=multiple_upload_fields,
        stored_data=stored_data,
        user=user,
    )
    if _synchronize_attachment_data(
        submission=submission,
        form=form,
        stored_data=stored_data,
    ):
        submission.data = stored_data
        submission.save(update_fields=["data", "updated_at"])

    updates = _json_value(
        form_definition.metadata_updates(form.cleaned_data, context),
    )
    if updates:
        application.metadata = {**application.metadata, **updates}
        application.save(update_fields=["metadata", "updated_at"])
    recalculate_progress(application, user=user)
    ApplicationEvent.objects.create(
        application=application,
        submission=submission,
        actor=user,
        kind=EventKind.FORM_SUBMITTED,
        title=_("%(form)s completed") % {"form": form_definition.name},
        description=(
            _("Submission %(number)s, revision %(revision)s was saved.")
            % {
                "number": submission.submission_number,
                "revision": submission.revision,
            }
            if form_definition.repeatable
            else _("Revision %(revision)s was saved.")
            % {"revision": submission.revision}
        ),
        payload={
            "form_key": form_key,
            "revision": submission.revision,
            "submission_number": submission.submission_number,
            "submission_mode": submission_mode,
            "valid_until": valid_until.isoformat() if valid_until else None,
        },
    )
    return submission


@transaction.atomic
def perform_application_action(
    *,
    application,
    action_key: str,
    user,
    cleaned_data: dict[str, Any] | None = None,
) -> tuple[ActionResult, ApplicationQueryThread | None]:
    application = ApplicationInstance.objects.select_for_update().get(
        pk=application.pk,
    )
    definition = registry.get(application.application_type)
    action = definition.get_action(action_key)
    if action is None:
        raise ValidationError(_("Unknown application action."))
    context = application_context(application, user)
    available, reason = action.availability(context)
    if not available:
        raise PermissionDenied(reason)

    result = action.perform(context, cleaned_data or {})
    status_before = application.status
    if result.new_status:
        application.status = result.new_status
    if result.metadata_updates:
        application.metadata = {
            **application.metadata,
            **_json_value(result.metadata_updates),
        }
    if result.outcome_updates:
        application.outcome = {
            **application.outcome,
            **_json_value(result.outcome_updates),
        }

    now = timezone.now()
    if result.new_status in {"submitted", "revision_submitted"}:
        application.submitted_at = now
    if result.new_status in {"approved", "rejected"}:
        application.decided_at = now
        application.decided_by = user

    query_thread = None
    if result.query:
        submission = (
            application.submissions.filter(
                form_key=result.query.form_key,
                is_current=True,
            ).first()
            if result.query.form_key
            else None
        )
        query_thread = ApplicationQueryThread.objects.create(
            application=application,
            submission=submission,
            subject=result.query.subject,
            status=result.query.initial_status,
            opened_by=user,
            assigned_to=(
                None
                if result.query.initial_status == QueryStatus.AWAITING_REVIEWER
                else user
            ),
            due_at=result.query.due_at,
        )
        ApplicationQueryMessage.objects.create(
            thread=query_thread,
            author=user,
            body=result.query.message,
        )

    application.save()
    ApplicationEvent.objects.create(
        application=application,
        actor=user,
        kind=(
            EventKind.STATUS_CHANGED
            if status_before != application.status
            else EventKind.ACTION
        ),
        title=result.message,
        description=str((cleaned_data or {}).get("note", "")),
        action_key=action_key,
        status_before=status_before,
        status_after=application.status,
        payload={"query_id": query_thread.pk if query_thread else None},
    )
    return result, query_thread


@transaction.atomic
def perform_form_action(
    *,
    application,
    form_key: str,
    action_key: str,
    user,
    cleaned_data: dict[str, Any] | None = None,
) -> FormActionResult:
    application = ApplicationInstance.objects.select_for_update().get(
        pk=application.pk,
    )
    definition = registry.get(application.application_type)
    form_definition = definition.get_form(form_key)
    if form_definition is None:
        raise ValidationError(_("Unknown application form."))
    action = form_definition.get_action(action_key)
    if action is None:
        raise ValidationError(_("Unknown form action."))

    context = application_context(application, user)
    submission = context.submissions.get(form_key)
    available, reason = action.availability(context, submission)
    if not available:
        raise PermissionDenied(reason)
    if submission is None:
        raise ValidationError(_("The form has not been completed."))

    result = action.perform(context, submission, cleaned_data or {})
    if result.metadata_updates:
        submission.metadata = {
            **submission.metadata,
            **_json_value(result.metadata_updates),
        }
        submission.save(update_fields=["metadata", "updated_at"])
    application.save(update_fields=["updated_at"])
    ApplicationEvent.objects.create(
        application=application,
        submission=submission,
        actor=user,
        kind=EventKind.ACTION,
        title=result.message,
        description=str((cleaned_data or {}).get("note", "")),
        action_key=f"{form_key}.{action_key}",
        status_before=application.status,
        status_after=application.status,
        payload={"form_key": form_key, "form_action_key": action_key},
    )
    return result


@transaction.atomic
def post_query_reply(*, thread, user, body: str) -> ApplicationQueryMessage:
    thread = (
        ApplicationQueryThread.objects.select_for_update()
        .select_related(
            "application",
        )
        .get(pk=thread.pk)
    )
    access = get_effective_access(thread.application, user)
    is_reviewer = access.allows(permission_keys.RAISE_QUERY)
    if not (
        access.allows(permission_keys.RESPOND_QUERIES)
        or access.allows(permission_keys.RAISE_QUERY)
    ):
        raise PermissionDenied(_("You cannot reply to this query."))
    if thread.status == QueryStatus.RESOLVED:
        raise ValidationError(_("A resolved query cannot receive replies."))

    message = ApplicationQueryMessage.objects.create(
        thread=thread,
        author=user,
        body=body,
    )
    thread.status = (
        QueryStatus.AWAITING_APPLICANT if is_reviewer else QueryStatus.AWAITING_REVIEWER
    )
    thread.save(update_fields=["status", "updated_at"])
    ApplicationEvent.objects.create(
        application=thread.application,
        actor=user,
        kind=EventKind.QUERY,
        title=_("Query response posted"),
        payload={"query_id": thread.pk},
    )
    return message


@transaction.atomic
def resolve_query(*, thread, user) -> None:
    thread = (
        ApplicationQueryThread.objects.select_for_update()
        .select_related(
            "application",
        )
        .get(pk=thread.pk)
    )
    access = get_effective_access(thread.application, user)
    if not access.allows(permission_keys.RESOLVE_QUERY):
        raise PermissionDenied(_("You cannot resolve this query."))
    if thread.status == QueryStatus.RESOLVED:
        return
    thread.status = QueryStatus.RESOLVED
    thread.resolved_at = timezone.now()
    thread.save(update_fields=["status", "resolved_at", "updated_at"])
    ApplicationQueryMessage.objects.create(
        thread=thread,
        author=user,
        body=_("Query resolved."),
        kind=QueryMessageKind.EVENT,
    )
    ApplicationEvent.objects.create(
        application=thread.application,
        actor=user,
        kind=EventKind.QUERY,
        title=_("Query resolved"),
        payload={"query_id": thread.pk},
    )


def assignable_roles(application, actor):
    definition = registry.get(application.application_type)
    access = get_effective_access(application, actor)
    audiences = set()
    if access.allows(permission_keys.MANAGE_APPLICANT_ACCESS):
        audiences.add("organisation")
    if access.allows(permission_keys.MANAGE_REVIEW_ACCESS):
        audiences.add("platform")
    if getattr(actor, "is_superuser", False):
        audiences.update({"organisation", "platform"})
    is_superuser = getattr(actor, "is_superuser", False)
    return tuple(
        role
        for role in definition.roles
        if role.audience in audiences
        and role.key != definition.owner_role_key
        and (is_superuser or role.permissions <= access.permissions)
    )


@transaction.atomic
def grant_application_access(  # noqa: C901
    *,
    application,
    actor,
    target_user,
    role_key: str,
    direct_permissions: list[str] | None = None,
) -> ApplicationAccess:
    application = ApplicationInstance.objects.select_for_update().get(
        pk=application.pk,
    )
    definition = registry.get(application.application_type)
    role = definition.get_role(role_key)
    if role is None or role not in assignable_roles(application, actor):
        raise PermissionDenied(_("You cannot assign that application role."))
    assigning_owner = (
        role.key == definition.owner_role_key
        and target_user.pk != application.created_by_id
    )
    if assigning_owner:
        raise PermissionDenied(_("Application ownership cannot be transferred here."))
    changing_owner = (
        target_user.pk == application.created_by_id
        and role.key != definition.owner_role_key
    )
    if changing_owner:
        raise PermissionDenied(_("The application owner's role cannot be changed."))
    outside_organisation = (
        role.audience == "organisation"
        and not application.organisation.memberships.filter(user=target_user).exists()
    )
    if outside_organisation:
        raise ValidationError(_("Applicant roles are limited to organisation members."))
    if role.audience == "platform" and not getattr(target_user, "is_ohc_team", False):
        raise ValidationError(_("Review roles are limited to OHC team members."))

    requested = set(direct_permissions or [])
    known = {item.key for item in definition.permissions}
    if requested - known:
        raise ValidationError(_("One or more permissions are not defined."))
    audience_permissions = set().union(
        *(
            candidate.permissions
            for candidate in definition.roles
            if candidate.audience == role.audience
        ),
    )
    if requested - audience_permissions:
        raise PermissionDenied(
            _("Those additional permissions belong to a different access audience."),
        )
    actor_permissions = get_effective_access(application, actor).permissions
    granted_permissions = role.permissions | requested
    if (
        not getattr(actor, "is_superuser", False)
        and not granted_permissions <= actor_permissions
    ):
        raise PermissionDenied(
            _("You cannot grant permissions that you do not have."),
        )
    existing_grant = ApplicationAccess.objects.filter(
        application=application,
        user=target_user,
    ).first()
    if existing_grant and not getattr(actor, "is_superuser", False):
        existing_role = definition.get_role(existing_grant.role_key)
        existing_permissions = set(existing_grant.direct_permissions)
        if existing_role:
            existing_permissions.update(existing_role.permissions)
        if not existing_permissions <= actor_permissions:
            raise PermissionDenied(
                _("You cannot change access with permissions beyond your own."),
            )
    grant, _created = ApplicationAccess.objects.update_or_create(
        application=application,
        user=target_user,
        defaults={
            "role_key": role.key,
            "direct_permissions": sorted(requested),
            "granted_by": actor,
        },
    )
    ApplicationEvent.objects.create(
        application=application,
        actor=actor,
        kind=EventKind.ACCESS_CHANGED,
        title=_("Application access updated"),
        description=_("%(user)s is now %(role)s.")
        % {"user": target_user.display_name, "role": role.label},
        payload={"user_id": target_user.pk, "role_key": role.key},
    )
    return grant


@transaction.atomic
def remove_application_access(*, application, actor, target_user) -> None:
    application = ApplicationInstance.objects.select_for_update().get(
        pk=application.pk,
    )
    grant = ApplicationAccess.objects.filter(
        application=application,
        user=target_user,
    ).first()
    if grant is None:
        return
    if target_user.pk == application.created_by_id:
        raise PermissionDenied(_("The application owner cannot be removed."))
    role = registry.get(application.application_type).get_role(grant.role_key)
    allowed_roles = assignable_roles(application, actor)
    if role is None or role not in allowed_roles:
        raise PermissionDenied(_("You cannot remove that application role."))
    actor_permissions = get_effective_access(application, actor).permissions
    target_permissions = role.permissions | set(grant.direct_permissions)
    if (
        not getattr(actor, "is_superuser", False)
        and not target_permissions <= actor_permissions
    ):
        raise PermissionDenied(
            _("You cannot manage access with permissions beyond your own."),
        )
    grant.delete()
    ApplicationEvent.objects.create(
        application=application,
        actor=actor,
        kind=EventKind.ACCESS_CHANGED,
        title=_("Application access removed"),
        description=_("Access was removed for %(user)s.")
        % {"user": target_user.display_name},
        payload={"user_id": target_user.pk},
    )
