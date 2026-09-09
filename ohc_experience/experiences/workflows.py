from collections import defaultdict
from uuid import uuid4

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import FormRecord
from ohc_experience.experiences.models import FormReuseScope
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.services import clone_current_attachments
from ohc_experience.experiences.services import create_application
from ohc_experience.experiences.services import form_field_schema
from ohc_experience.experiences.services import store_uploads
from ohc_experience.experiences.services import submission_payload
from ohc_experience.experiences.services import synchronize_attachment_data
from ohc_experience.organisations.models import Organisation

from .models import AuditEvent
from .models import Milestone
from .models import Notification
from .models import ProductWorkspace
from .models import ReviewItem
from .models import ReviewQuery
from .permissions import can_integrate
from .permissions import eligible_reviewer
from .permissions import require_decider
from .permissions import require_integrator
from .permissions import visible_reviews
from .permissions import visible_tickets
from .registry import get_program
from .registry import registry
from .services import issue_outcome

MAX_REVIEW_TEXT = 10000


def _lock_review(pk):
    item = ReviewItem.objects.only("organisation_id").get(pk=pk)
    # Product changes and milestone submissions share an eligibility lock.
    Organisation.objects.select_for_update().get(pk=item.organisation_id)
    return (
        ReviewItem.objects.select_for_update(of=("self",))
        .select_related(
            "selected_submission",
            "form",
            "organisation",
            "product",
            "application",
        )
        .get(pk=pk)
    )


def audit(*, actor, action, item=None, product=None, organisation=None, detail=None):  # noqa: PLR0913
    product = product or (item.product if item else None)
    organisation = organisation or (item.organisation if item else product.organisation)
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        item=item,
        product=product,
        organisation=organisation,
        detail=detail or {},
    )


def notify_integrators(organisation, subject, body):
    recipients = (
        organisation.memberships.filter(user__is_active=True)
        .values_list("user__email", flat=True)
        .distinct()
    )
    Notification.objects.bulk_create(
        [
            Notification(recipient=email, subject=subject, body=body)
            for email in recipients
        ],
    )


def notify_ticket_reply(ticket, actor, body):
    subject = f"{get_program().short_name}: reply to {ticket.reference}"
    if can_integrate(actor, ticket.organisation):
        if (
            ticket.assignee_id
            and visible_tickets(ticket.assignee).filter(pk=ticket.pk).exists()
        ):
            Notification.objects.create(
                recipient=ticket.assignee.email,
                subject=subject,
                body=body,
            )
    else:
        notify_integrators(ticket.organisation, subject, body)


def notify_reviewers(item, subject, body):
    recipients = (
        [item.assignee.email]
        if item.assignee_id
        and visible_reviews(item.assignee).filter(pk=item.pk).exists()
        else list(
            get_user_model()
            .objects.filter(is_active=True, is_superuser=True)
            .values_list("email", flat=True),
        )
    )
    Notification.objects.bulk_create(
        [
            Notification(recipient=email, subject=subject, body=body)
            for email in recipients
        ],
    )


@transaction.atomic
def organisation_review(organisation, user):
    require_integrator(user, organisation)
    type(organisation).objects.select_for_update().get(pk=organisation.pk)
    program = get_program()
    definition = program.organisation_form
    record, _ = FormRecord.objects.get_or_create(
        organisation=organisation,
        reuse_scope=FormReuseScope.ORGANISATION,
        form_key=definition.key,
        defaults={
            "reference": f"FORM-{uuid4().hex[:16].upper()}",
            "name": definition.name,
            "created_by": user,
            "metadata": {"program": program.key},
        },
    )
    item, _ = ReviewItem.objects.get_or_create(
        organisation=organisation,
        kind=ReviewItem.Kind.ORGANISATION,
        defaults={"form": record},
    )
    return item


def build_form(item, *, data=None, files=None, draft=False):
    submission = item.selected_submission
    existing = defaultdict(list)
    if submission:
        for attachment in submission.attachments.filter(is_current=True):
            existing[attachment.field_key].append(attachment)
    initial = dict(submission.data) if submission else {}
    if not submission:
        initial.update(item.definition.initial_data(item))
    form_class = item.definition.form_class
    for key, field in form_class.base_fields.items():
        if isinstance(field, forms.FileField):
            initial.pop(key, None)
    form = form_class(
        data=data,
        files=files,
        initial=initial,
        existing_files=dict(existing),
        draft=draft,
        **item.definition.form_kwargs(item),
    )
    form.schema_version = item.definition.schema_version
    return form


def _complete_submission(item, actor):
    resubmitting = item.submitted_at is not None
    item.resubmission_count += int(resubmitting)
    item.submitted_at = timezone.now()
    if item.definition.auto_approve:
        _auto_approve(item, actor)
    else:
        _request_review(item, resubmitting=resubmitting)
    audit(
        actor=actor,
        action="Resubmitted for review" if resubmitting else "Requested review",
        item=item,
    )


def _request_review(item, *, resubmitting):
    item.status = (
        ReviewItem.Status.IN_REVIEW
        if resubmitting or item.assignee_id
        else ReviewItem.Status.NEW
    )
    item.decided_at = None
    item.decided_by = None
    item.decision_note = ""
    _set_application_status(item, "under_review")
    notify_reviewers(
        item,
        f"{item.program.short_name}: {item.reference} received",
        f"{item.title} is ready for review.",
    )


def _snapshot(item, form, actor, *, completed):
    record = FormRecord.objects.select_for_update().get(pk=item.form_id)
    old = item.selected_submission
    data, uploads, multiple = submission_payload(form, old.data if old else {})
    record.submissions.filter(is_current=True).update(is_current=False)
    # A new application gets a new occurrence; edits remain revisions of its pin.
    if old and old.origin_application_id == item.application_id:
        number = old.submission_number
        revision = (
            record.submissions.filter(submission_number=number).aggregate(
                value=Max("revision"),
            )["value"]
            + 1
        )
    else:
        number = (
            record.submissions.aggregate(value=Max("submission_number"))["value"] or 0
        ) + 1
        revision = 1
    snapshot = FormSubmission.objects.create(
        form=record,
        form_key=record.form_key,
        origin_application=item.application,
        data=data,
        field_schema=form_field_schema(form),
        schema_version=form.schema_version,
        metadata={"complete": completed},
        valid_until=item.definition.snapshot_valid_until(form),
        status="completed" if completed else "needs_changes",
        submission_number=number,
        revision=revision,
        submitted_by=actor,
    )
    clone_current_attachments(source=old, destination=snapshot, form=form)
    store_uploads(
        submission=snapshot,
        uploads=uploads,
        multiple_upload_fields=multiple,
        stored_data=data,
        user=actor,
    )
    synchronize_attachment_data(submission=snapshot, form=form, stored_data=data)
    snapshot.data = data
    snapshot.save(update_fields=["data"])
    item.selected_submission = snapshot
    if item.application_id:
        ApplicationFormUse.objects.filter(
            application=item.application,
            form=record,
        ).update(selected_submission=snapshot)
    audit(
        actor=actor,
        action="Form submitted" if completed else "Draft saved",
        item=item,
        detail={
            "submission_id": snapshot.pk,
            "before": old.data if old else {},
            "after": data,
        },
    )
    return snapshot


def milestone_locked(item):
    if not item.application_id:
        return ""
    application = item.application
    milestone = getattr(application, "milestone", None)
    if milestone and not milestone.enabled:
        return "This milestone is not applied for. Edit the product to add it."
    for dependency in application.dependencies.all():
        if (
            dependency.status
            not in registry.get(dependency.application_type).success_statuses
        ):
            return f"{application.title} unlocks once {dependency.title} is approved."
    return ""


def can_edit_review(item):
    return item.editable or (
        item.status == ReviewItem.Status.APPROVED
        and item.definition.allow_approved_updates
    )


def unlock_dependants(application):
    for dependent in application.dependent_applications.filter(status="locked"):
        if all(
            dependency.status
            in registry.get(dependency.application_type).success_statuses
            for dependency in dependent.dependencies.all()
        ):
            dependent.status = "draft"
            dependent.save(update_fields=["status", "updated_at"])


def _set_application_status(item, status):
    if item.application_id:
        item.application.status = status
        item.application.submitted_at = item.submitted_at
        item.application.decided_at = item.decided_at
        item.application.decided_by = item.decided_by
        item.application.save(
            update_fields=[
                "status",
                "submitted_at",
                "decided_at",
                "decided_by",
                "updated_at",
            ],
        )


def project_product(item, actor, *, product_values, solution_type, selections):
    product = item.product
    workspace = ProductWorkspace.objects.select_for_update().get(product=product)
    program = workspace.definition
    new_keys = program.milestone_keys(selections)
    approved = set(
        product.milestones.filter(application__status="approved").values_list(
            "key",
            flat=True,
        ),
    )
    required_selections = {
        value
        for value in workspace.applied_milestones
        if value.split(":", 1)[1] in approved
    }
    if not required_selections.issubset(selections):
        msg = "Approved milestones cannot be removed from the product."
        raise ValidationError(msg)
    active = product.milestones.filter(
        application__review_item__status__in=["new", "in_review", "query_raised"],
    )
    if active.exclude(key__in=new_keys).exists():
        msg = "Withdraw an active application before removing its milestone."
        raise ValidationError(
            msg,
        )
    for key, value in product_values.items():
        setattr(product, key, value)
    product.save(update_fields=["name", "description", "product_type", "updated_at"])
    workspace.solution_type = solution_type
    workspace.applied_milestones = selections
    workspace.registration_status = "pending"
    workspace.save()
    product.milestones.exclude(key__in=new_keys).update(enabled=False)
    for key in program.ordered_milestones():
        if key not in new_keys:
            continue
        milestone = product.milestones.filter(key=key).first()
        if milestone:
            milestone.enabled = True
            milestone.save(update_fields=["enabled"])
            continue
        definition = program.milestones[key]
        dependencies = list(
            product.milestones.filter(key=definition.predecessor).values_list(
                "application_id",
                flat=True,
            ),
        )
        application = create_application(
            application_type=program.application_for(key).key,
            product=product,
            user=actor,
        )
        application.title = f"{definition.code} - {definition.name}"
        application.metadata["milestone"] = key
        application.status = "locked" if definition.predecessor else "draft"
        application.save()
        for dependency_id in dependencies:
            ApplicationDependency.objects.create(
                application=application,
                depends_on_id=dependency_id,
            )
        Milestone.objects.create(product=product, key=key, application=application)
        use = application.form_uses.get()
        ReviewItem.objects.create(
            kind=ReviewItem.Kind.APPLICATION,
            organisation=item.organisation,
            product=product,
            application=application,
            form=use.form,
            selected_submission=use.selected_submission,
        )
        audit(
            actor=actor,
            action=f"Milestone added: {definition.name}",
            item=item,
            detail={"milestone": key},
        )


@transaction.atomic
def save_review_form(  # noqa: PLR0913
    item,
    actor,
    *,
    data,
    files=None,
    submit=False,
    expected_revision=None,
):
    item = _lock_review(item.pk)
    require_integrator(actor, item.organisation)
    if not can_edit_review(item):
        msg = "Withdraw this request before editing the submitted form."
        raise ValidationError(
            msg,
        )
    if expected_revision is not None and str(item.selected_submission_id or "") != str(
        expected_revision,
    ):
        msg = "A teammate updated this form. Reload the page before saving."
        raise ValidationError(
            msg,
        )
    reason = milestone_locked(item)
    if reason:
        raise ValidationError(reason)
    if submit:
        reason = item.definition.submission_block_reason(item)
        if reason:
            raise ValidationError(reason)
    form = build_form(item, data=data, files=files, draft=not submit)
    if not form.is_valid():
        return item, form, False
    if submit:
        item.definition.on_submit(item, form.cleaned_data, actor)
    _snapshot(item, form, actor, completed=submit)
    if submit:
        _complete_submission(item, actor)
    else:
        if item.status != ReviewItem.Status.SENT_BACK:
            item.status = ReviewItem.Status.DRAFT
        _set_application_status(item, "draft")
    item.save()
    return item, form, True


@transaction.atomic
def certification_review(product, actor):
    """Continue an open certification request, or begin a new review cycle."""
    require_integrator(actor, product.organisation)
    Organisation.objects.select_for_update().get(pk=product.organisation_id)
    definition = product.workspace.definition.certification_application
    if definition is None:
        msg = "This program does not offer product certification reviews."
        raise ValidationError(msg)
    existing = (
        product.review_items.filter(application__application_type=definition.key)
        .exclude(status=ReviewItem.Status.APPROVED)
        .order_by("-created_at", "-pk")
        .first()
    )
    if existing:
        return existing
    application = create_application(
        application_type=definition.key,
        product=product,
        user=actor,
    )
    use = application.form_uses.get()
    # Renewals share their form identity, but start with fresh evidence. Never
    # turn the previous certificate into a newly submitted renewal by default.
    use.selected_submission = None
    use.save(update_fields=["selected_submission", "updated_at"])
    item = ReviewItem.objects.create(
        kind=ReviewItem.Kind.APPLICATION,
        organisation=product.organisation,
        product=product,
        application=application,
        form=use.form,
    )
    audit(actor=actor, action="Certification review started", item=item)
    return item


@transaction.atomic
def register_product(organisation, actor, *, data, program=None):
    require_integrator(actor, organisation)
    organisation = Organisation.objects.select_for_update().get(pk=organisation.pk)
    program = program or get_program()
    definition = program.product_application
    form = definition.forms[0].form_class(data=data)
    if not form.is_valid():
        return None, form
    product = Product.objects.create(
        organisation=organisation,
        created_by=actor,
        **program.product_values(form.cleaned_data),
    )
    workspace = ProductWorkspace.objects.create(
        product=product,
        experience_type=program.key,
        reference=f"{program.product_reference_prefix}-{timezone.localdate().year}-{product.pk:05d}",
    )
    application = create_application(
        application_type=definition.key,
        product=product,
        user=actor,
    )
    use = application.form_uses.get(form_key=definition.forms[0].key)
    item = ReviewItem.objects.create(
        kind=ReviewItem.Kind.PRODUCT,
        organisation=organisation,
        product=product,
        application=application,
        form=use.form,
    )
    save_review_form(item, actor, data=data, submit=True)
    program.on_product_created(product, actor)
    return workspace, form


@transaction.atomic
def withdraw(item, actor):
    item = _lock_review(item.pk)
    require_integrator(actor, item.organisation)
    if not item.pending:
        msg = "Only an active review request can be withdrawn."
        raise ValidationError(msg)
    item.status = ReviewItem.Status.DRAFT
    item.save(update_fields=["status"])
    _set_application_status(item, "draft")
    audit(actor=actor, action="Request withdrawn", item=item)
    notify_reviewers(
        item,
        f"{item.program.short_name}: {item.reference} withdrawn",
        f"{item.title} was withdrawn by the integrator.",
    )


@transaction.atomic
def reuse_evidence(item, actor):
    item = _lock_review(item.pk)
    require_integrator(actor, item.organisation)
    if not item.definition.allow_reuse or not item.editable or milestone_locked(item):
        msg = "Evidence cannot be reused in this state."
        raise ValidationError(msg)
    source = (
        item.form.submissions.filter(
            status="completed",
            schema_version=item.definition.form_class.schema_version,
        )
        .exclude(pk=item.selected_submission_id)
        .order_by("-submitted_at")
        .first()
    )
    if not source:
        msg = "No completed evidence is available for this product yet."
        raise ValidationError(
            msg,
        )
    item.selected_submission = source
    item.save(update_fields=["selected_submission"])
    ApplicationFormUse.objects.filter(
        application=item.application,
        form=item.form,
    ).update(selected_submission=source)
    audit(
        actor=actor,
        action="Reused product evidence",
        item=item,
        detail={"submission_id": source.pk},
    )


@transaction.atomic
def assign_review(item, actor, assignee):
    if not actor.is_superuser:
        msg = "Only administrators can assign reviewers."
        raise PermissionDenied(msg)
    item = _lock_review(item.pk)
    if assignee and not eligible_reviewer(assignee, item):
        msg = "Choose an active reviewer with write or approve access to this category."
        raise ValidationError(msg)
    before = item.assignee_id
    item.assignee = assignee
    if item.status == ReviewItem.Status.NEW and assignee:
        item.status = ReviewItem.Status.IN_REVIEW
    item.save(update_fields=["assignee", "status"])
    audit(
        actor=actor,
        action="Reviewer assigned" if assignee else "Reviewer unassigned",
        item=item,
        detail={"before": before, "after": getattr(assignee, "pk", None)},
    )
    if assignee:
        notify_reviewers(
            item,
            f"{item.program.short_name}: {item.reference} assigned to you",
            f"Review {item.title} in the review queue.",
        )


def _approve_subject(item, actor):
    if item.application_id:
        unlock_dependants(item.application)
    for outcome in item.definition.on_approve(item, actor):
        issue_outcome(application=item.application, actor=actor, outcome=outcome)


def _validate_approval(item):
    reason = item.definition.approval_block_reason(item)
    if reason:
        raise ValidationError(reason)
    if (
        item.queries.filter(submission=item.selected_submission)
        .exclude(status="resolved")
        .exists()
    ):
        msg = "Resolve all queries on this submission before approving."
        raise ValidationError(msg)


def _auto_approve(item, actor):
    """Submitting is the decision. Nobody is asked, but the record still lands
    in the queue so a reviewer can read it."""
    _validate_approval(item)
    item.status = ReviewItem.Status.APPROVED
    item.decided_at = timezone.now()
    item.decided_by = None
    item.decision_note = ""
    _set_application_status(item, "approved")
    _approve_subject(item, actor)
    notify_reviewers(
        item,
        f"{item.program.short_name}: {item.reference} recorded",
        f"{item.title} needs no decision and has been recorded.",
    )


@transaction.atomic
def decide(item, actor, *, action, note="", field_key="form"):
    item = _lock_review(item.pk)
    require_decider(actor, item, "write" if action == "query" else "approve")
    if not item.pending:
        msg = "This item is not awaiting a decision."
        raise ValidationError(msg)
    note = note.strip()
    if action not in {"approve", "send_back", "query"}:
        msg = "Choose a valid review action."
        raise ValidationError(msg)
    if action in {"send_back", "query"} and not note:
        msg = "Enter a reason or question before continuing."
        raise ValidationError(msg)
    if len(note) > MAX_REVIEW_TEXT:
        msg = "Use no more than 10,000 characters."
        raise ValidationError(msg)
    if action == "query":
        valid_keys = {
            "form",
            *(field["key"] for field in item.selected_submission.field_schema),
        }
        if field_key not in valid_keys:
            msg = "Choose a field from the submitted form."
            raise ValidationError(msg)
        query = ReviewQuery.objects.create(
            item=item,
            submission=item.selected_submission,
            field_key=field_key,
            question=note,
            raised_by=actor,
        )
        item.status = ReviewItem.Status.QUERY
        _set_application_status(item, "query_raised")
        audit(
            actor=actor,
            action="Query raised",
            item=item,
            detail={"query_id": query.pk, "field": field_key, "question": note},
        )
    else:
        if action == "approve":
            _validate_approval(item)
        item.status = (
            ReviewItem.Status.APPROVED
            if action == "approve"
            else ReviewItem.Status.SENT_BACK
        )
        item.decided_at, item.decided_by, item.decision_note = (
            timezone.now(),
            actor,
            note,
        )
        _set_application_status(item, "approved" if action == "approve" else "draft")
        if action == "approve":
            _approve_subject(item, actor)
        else:
            item.definition.on_send_back(item, actor)
        audit(
            actor=actor,
            action="Approved" if action == "approve" else "Sent back",
            item=item,
            detail={"note": note, "submission_id": item.selected_submission_id},
        )
    item.save()
    notify_integrators(
        item.organisation,
        f"{item.program.short_name}: {item.reference} - {item.get_status_display()}",
        f"{item.title}\n\n{note}\n\nReview the record in the portal.",
    )
    return item


@transaction.atomic
def reply_query(query, actor, body):
    item = _lock_review(query.item_id)
    query = ReviewQuery.objects.get(pk=query.pk)
    require_integrator(actor, item.organisation)
    if (
        not item.pending
        or query.submission_id != item.selected_submission_id
        or query.status != "open"
    ):
        msg = "This query no longer accepts replies."
        raise ValidationError(msg)
    if not body.strip() or len(body) > MAX_REVIEW_TEXT:
        msg = "Enter a reply of up to 10,000 characters."
        raise ValidationError(msg)
    query.reply, query.replied_by, query.replied_at, query.status = (
        body.strip(),
        actor,
        timezone.now(),
        "answered",
    )
    query.save()
    if not item.queries.filter(
        submission=item.selected_submission,
        status="open",
    ).exists():
        item.status = ReviewItem.Status.IN_REVIEW
        item.save(update_fields=["status"])
        _set_application_status(item, "under_review")
    audit(
        actor=actor,
        action="Query answered",
        item=item,
        detail={"query_id": query.pk, "reply": body.strip()},
    )
    notify_reviewers(
        item,
        f"{item.program.short_name}: {item.reference} query answered",
        f"{item.title}\n\n{body.strip()}",
    )


@transaction.atomic
def resolve_query(query, actor):
    item = _lock_review(query.item_id)
    query = ReviewQuery.objects.get(pk=query.pk)
    require_decider(actor, item, "write")
    if (
        query.status != "answered"
        or not item.pending
        or query.submission_id != item.selected_submission_id
    ):
        msg = "Only an answered query on the current review can be resolved."
        raise ValidationError(
            msg,
        )
    query.status, query.resolved_at = "resolved", timezone.now()
    query.save(update_fields=["status", "resolved_at"])
    audit(
        actor=actor,
        action="Query resolved",
        item=item,
        detail={"query_id": query.pk},
    )
