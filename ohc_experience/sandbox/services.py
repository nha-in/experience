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
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.services import clone_current_attachments
from ohc_experience.experiences.services import create_application
from ohc_experience.experiences.services import form_field_schema
from ohc_experience.experiences.services import store_uploads
from ohc_experience.experiences.services import submission_payload
from ohc_experience.experiences.services import synchronize_attachment_data
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.users.permissions import is_ohc_team

from .catalog import MILESTONES
from .catalog import canonical_keys
from .forms import ExitEvidenceForm
from .forms import OrganisationForm
from .forms import ProductRegistrationForm
from .models import AuditEvent
from .models import Milestone
from .models import Notification
from .models import ProductWorkspace
from .models import ReviewItem
from .models import ReviewQuery

FORM_CLASSES = {
    ReviewItem.Kind.ORGANISATION: OrganisationForm,
    ReviewItem.Kind.PRODUCT: ProductRegistrationForm,
    ReviewItem.Kind.EXIT: ExitEvidenceForm,
}
MAX_REVIEW_TEXT = 10000


def _lock_review(pk):
    item = ReviewItem.objects.only("organisation_id").get(pk=pk)
    # Product changes and milestone submissions share an eligibility lock.
    Organisation.objects.select_for_update().get(pk=item.organisation_id)
    return (
        ReviewItem.objects.select_for_update(of=("self",))
        .select_related(
            "selected_submission", "form", "organisation", "product", "application",
        )
        .get(pk=pk)
    )


def reviewer(user):
    return bool(user.is_authenticated and (user.is_superuser or is_ohc_team(user)))


def can_integrate(user, organisation):
    return bool(
        user.is_authenticated
        and not reviewer(user)
        and organisation.memberships.filter(
            user=user,
            role__in=[Role.OWNER, Role.ADMIN, Role.DEVELOPER],
        ).exists(),
    )


def require_integrator(user, organisation):
    if not can_integrate(user, organisation):
        msg = "Only this organisation's integrators can change its submissions."
        raise PermissionDenied(
            msg,
        )


def can_decide(user, item):
    return reviewer(user) and (user.is_superuser or item.assignee_id == user.pk)


def require_decider(user, item):
    if not can_decide(user, item):
        msg = "An administrator must assign this review to you first."
        raise PermissionDenied(msg)


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


def notify_reviewers(item, subject, body):
    recipients = (
        [item.assignee.email]
        if item.assignee_id
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
    # Serialise initialisation for concurrent first visits by two team members.
    type(organisation).objects.select_for_update().get(pk=organisation.pk)
    record, _ = FormRecord.objects.get_or_create(
        organisation=organisation,
        reuse_scope=FormReuseScope.ORGANISATION,
        form_key="sandbox_organisation",
        defaults={
            "reference": f"FORM-{uuid4().hex[:16].upper()}",
            "name": "Organisation verification",
            "created_by": user,
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
    if item.kind == ReviewItem.Kind.ORGANISATION and not submission:
        initial.update(name=item.organisation.name, website=item.organisation.website)
        initial["entity_type"] = item.form.metadata.get(
            "entity_type",
            "private_company",
        )
    form_class = FORM_CLASSES[item.kind]
    for key, field in form_class.base_fields.items():
        if isinstance(field, forms.FileField):
            initial.pop(key, None)
    return form_class(
        data=data,
        files=files,
        initial=initial,
        existing_files=dict(existing),
        draft=draft,
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
    if item.kind != ReviewItem.Kind.EXIT:
        return ""
    milestone = item.application.milestone
    if not milestone.enabled:
        return "This milestone is not applied for. Edit the product to add it."
    predecessor = milestone.definition.predecessor
    if (
        predecessor
        and not Milestone.objects.filter(
            product=item.product,
            key=predecessor,
            enabled=True,
            application__status="approved",
        ).exists()
    ):
        return (
            f"{milestone.definition.code} unlocks once "
            f"{MILESTONES[predecessor].code} is approved."
        )
    return ""


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


def _project_organisation(item, data):
    org = item.organisation
    org.name = data["name"]
    org.legal_name = data["name"]
    org.website = data["website"]
    org.state = data["state"]
    org.city = data["district"]
    org.onboarded_at = org.onboarded_at or timezone.now()
    org.verification_status = "pending"
    org.verified_at = None
    org.save()


def _project_product(item, data, actor):
    product = item.product
    workspace = ProductWorkspace.objects.select_for_update().get(product=product)
    new_keys = canonical_keys(data["applied_milestones"])
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
    if not required_selections.issubset(data["applied_milestones"]):
        msg = "Approved milestones cannot be removed from the product."
        raise ValidationError(msg)
    active = product.milestones.filter(
        application__sandbox_review__status__in=["new", "in_review", "query_raised"],
    )
    if active.exclude(key__in=new_keys).exists():
        msg = "Withdraw an active exit request before removing its milestone."
        raise ValidationError(
            msg,
        )
    product.name, product.description, product.product_type = (
        data["name"],
        data["description"],
        data["category"],
    )
    product.save(update_fields=["name", "description", "product_type", "updated_at"])
    workspace.solution_type = data["solution_type"]
    workspace.applied_milestones = data["applied_milestones"]
    workspace.registration_status = "pending"
    workspace.save()
    product.milestones.exclude(key__in=new_keys).update(enabled=False)
    for key in MILESTONES:
        if key not in new_keys:
            continue
        milestone = product.milestones.filter(key=key).first()
        if milestone:
            milestone.enabled = True
            milestone.save(update_fields=["enabled"])
            continue
        definition = MILESTONES[key]
        dependencies = list(
            product.milestones.filter(key=definition.predecessor).values_list(
                "application_id",
                flat=True,
            ),
        )
        application = create_application(
            application_type="abdm_sandbox_exit",
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
            kind=ReviewItem.Kind.EXIT,
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
def save_review_form(  # noqa: C901, PLR0913
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
    can_amend = (
        item.kind in {ReviewItem.Kind.ORGANISATION, ReviewItem.Kind.PRODUCT}
        and item.status == ReviewItem.Status.APPROVED
    )
    if not item.editable and not can_amend:
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
    if submit and item.kind == ReviewItem.Kind.EXIT:
        if (
            not item.organisation.is_verified
            or item.product.workspace.registration_status != "registered"
        ):
            msg = (
                "Organisation verification and product registration must be "
                "approved before requesting exit."
            )
            raise ValidationError(
                msg,
            )
    form = build_form(item, data=data, files=files, draft=not submit)
    if not form.is_valid():
        return item, form, False
    if submit and item.kind == ReviewItem.Kind.PRODUCT:
        _project_product(item, form.cleaned_data, actor)
    if submit and item.kind == ReviewItem.Kind.ORGANISATION:
        _project_organisation(item, form.cleaned_data)
        # Amending legal identity suspends previously issued credentials.
        from .credentials import suspend_organisation_credentials  # noqa: PLC0415

        suspend_organisation_credentials(item.organisation, actor)
    _snapshot(item, form, actor, completed=submit)
    if submit:
        resubmitting = item.submitted_at is not None
        item.resubmission_count += int(resubmitting)
        item.submitted_at = timezone.now()
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
            f"ABDM: {item.reference} received",
            f"{item.title} is ready for review.",
        )
        audit(
            actor=actor,
            action="Resubmitted for review" if resubmitting else "Requested review",
            item=item,
        )
    else:
        if item.status != ReviewItem.Status.SENT_BACK:
            item.status = ReviewItem.Status.DRAFT
        _set_application_status(item, "draft")
    item.save()
    return item, form, True


@transaction.atomic
def register_product(organisation, actor, *, data):
    require_integrator(actor, organisation)
    organisation = Organisation.objects.select_for_update().get(pk=organisation.pk)
    form = ProductRegistrationForm(data=data)
    if not form.is_valid():
        return None, form
    product = Product.objects.create(
        organisation=organisation,
        name=form.cleaned_data["name"],
        description=form.cleaned_data["description"],
        product_type=form.cleaned_data["category"],
        created_by=actor,
    )
    workspace = ProductWorkspace.objects.create(
        product=product,
        sandbox_id=f"SBX-{timezone.localdate().year}-{product.pk:05d}",
        solution_type=form.cleaned_data["solution_type"],
    )
    application = create_application(
        application_type="abdm_sandbox_product",
        product=product,
        user=actor,
    )
    use = application.form_uses.get()
    item = ReviewItem.objects.create(
        kind=ReviewItem.Kind.PRODUCT,
        organisation=organisation,
        product=product,
        application=application,
        form=use.form,
    )
    save_review_form(item, actor, data=data, submit=True)
    if organisation.is_verified:
        from .credentials import issue_credentials  # noqa: PLC0415

        issue_credentials(product, actor)
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
        f"ABDM: {item.reference} withdrawn",
        f"{item.title} was withdrawn by the integrator.",
    )


@transaction.atomic
def reuse_evidence(item, actor):
    item = _lock_review(item.pk)
    require_integrator(actor, item.organisation)
    if item.kind != ReviewItem.Kind.EXIT or not item.editable or milestone_locked(item):
        msg = "Evidence cannot be reused in this state."
        raise ValidationError(msg)
    source = (
        item.form.submissions.filter(
            status="completed",
            schema_version=ExitEvidenceForm.schema_version,
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
    if assignee and (not assignee.is_active or not reviewer(assignee)):
        msg = "Choose an active NHA reviewer."
        raise ValidationError(msg)
    item = _lock_review(item.pk)
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
            f"ABDM: {item.reference} assigned to you",
            f"Review {item.title} in the NHA queue.",
        )


def _approve_subject(item, actor):
    if item.kind == ReviewItem.Kind.ORGANISATION:
        item.organisation.set_verification("verified")
        from .credentials import issue_credentials  # noqa: PLC0415

        for product in item.organisation.products.filter(workspace__isnull=False):
            issue_credentials(product, actor)
    elif item.kind == ReviewItem.Kind.PRODUCT:
        ProductWorkspace.objects.filter(product=item.product).update(
            registration_status="registered",
            registered_at=item.decided_at,
        )
    else:
        for milestone in item.product.milestones.filter(
            enabled=True,
            application__status="locked",
        ):
            if milestone.definition.predecessor == item.application.milestone.key:
                type(item.application).objects.filter(
                    pk=milestone.application_id,
                ).update(status="draft")
        ProductOutcome.objects.update_or_create(
            product=item.product,
            source_application=item.application,
            outcome_type="milestone_approval",
            defaults={
                "name": f"{item.application.title} approved",
                "issued_by": actor,
                "data": {
                    "milestone": item.application.milestone.key,
                    "approved_on": item.decided_at.isoformat(),
                    "decision_note": item.decision_note,
                    "production_handoff": (
                        "Production credentials are issued separately "
                        "by the gateway team."
                    ),
                },
            },
        )


@transaction.atomic
def decide(item, actor, *, action, note="", field_key="form"):  # noqa: C901
    item = _lock_review(item.pk)
    require_decider(actor, item)
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
        if (
            action == "approve"
            and item.queries.filter(submission=item.selected_submission)
            .exclude(status="resolved")
            .exists()
        ):
            msg = "Resolve all queries on this submission before approving."
            raise ValidationError(
                msg,
            )
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
        elif item.kind == ReviewItem.Kind.ORGANISATION:
            item.organisation.set_verification("sent_back")
        elif item.kind == ReviewItem.Kind.PRODUCT:
            ProductWorkspace.objects.filter(product=item.product).update(
                registration_status="sent_back",
            )
        audit(
            actor=actor,
            action="Approved" if action == "approve" else "Sent back",
            item=item,
            detail={"note": note, "submission_id": item.selected_submission_id},
        )
    item.save()
    notify_integrators(
        item.organisation,
        f"ABDM: {item.reference} - {item.get_status_display()}",
        f"{item.title}\n\n{note}\n\nReview the record in the sandbox portal.",
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
        f"ABDM: {item.reference} query answered",
        f"{item.title}\n\n{body.strip()}",
    )


@transaction.atomic
def resolve_query(query, actor):
    item = _lock_review(query.item_id)
    query = ReviewQuery.objects.get(pk=query.pk)
    require_decider(actor, item)
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
