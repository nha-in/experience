import logging
from collections import defaultdict
from uuid import uuid4

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Q
from django.utils import timezone

from ohc_experience.experiences.definitions import Prerequisite
from ohc_experience.experiences.definitions import readable_list
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import ApplicationInstance
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
from .notifications import notify_decision
from .notifications import notify_review
from .permissions import eligible_reviewer
from .permissions import require_decider
from .permissions import require_integrator
from .permissions import visible_reviews
from .registry import get_program
from .registry import registry
from .services import issue_outcome

logger = logging.getLogger(__name__)

MAX_REVIEW_TEXT = 10000
PENDING_STATUSES = (
    ReviewItem.Status.NEW,
    ReviewItem.Status.IN_REVIEW,
    ReviewItem.Status.QUERY,
)


def _notice(item, event, *, note=""):
    try:
        notify_review(item, event, note=note)
    except Exception:
        # Email must never break a workflow transition.
        logger.exception("Failed to email the %s notice for %s", event, item.reference)


def _announce(item, action, note):
    """Only an approval leaves the review thread; the rest keep its subject."""
    if action != "approve":
        _notice(item, "query_raised" if action == "query" else "sent_back", note=note)
        return
    try:
        notify_decision(item, note)
    except Exception:
        logger.exception("Failed to email the approval for %s", item.reference)


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
    updating = item.status == ReviewItem.Status.APPROVED
    item.resubmission_count += int(resubmitting)
    item.submitted_at = timezone.now()
    if item.definition.auto_approve and not pending_prerequisites(item):
        _auto_approve(item, actor)
        action = "Record updated" if updating else "Recorded"
    else:
        _request_review(item, resubmitting=resubmitting)
        action = "Resubmitted for review" if resubmitting else "Requested review"
    audit(actor=actor, action=action, item=item)


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
    _notice(item, "received")


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


def milestone_unavailable(item):
    milestone = (
        getattr(item.application, "milestone", None) if item.application_id else None
    )
    if milestone and not milestone.enabled:
        return "This milestone is not applied for. Edit the product to add it."
    unsubmitted = unsubmitted_prerequisites(item)
    if unsubmitted:
        names = readable_list(review.title for review in unsubmitted)
        verb = "is" if len(unsubmitted) == 1 else "are"
        return f"{item.application.title} opens once {names} {verb} submitted."
    return ""


def _prerequisite_applications(application):
    """Everything an application builds on, directly or not, earliest first."""
    ordered, seen = [], {application.pk}

    def visit(current):
        for dependency in current.dependencies.select_related(
            "review_item",
            "milestone",
        ):
            if dependency.pk not in seen:
                seen.add(dependency.pk)
                visit(dependency)
                ordered.append(dependency)

    visit(application)
    return ordered


def _dependant_applications(application):
    """Everything built on an application, directly or not, earliest first."""
    ordered, seen = [], {application.pk}

    def visit(current):
        for dependant in current.dependent_applications.select_related(
            "review_item",
        ).order_by("pk"):
            if dependant.pk not in seen:
                seen.add(dependant.pk)
                ordered.append(dependant)
                visit(dependant)

    visit(application)
    return ordered


def _reviews(applications):
    return [
        review
        for review in (
            getattr(application, "review_item", None) for application in applications
        )
        if review
    ]


def unsubmitted_prerequisites(item):
    """Reviews this one builds on that were never submitted, or were withdrawn.

    A form opens once everything before it is submitted, so a chain is worked
    through in order. Sent back still counts as submitted: a reviewer's decision
    never closes a form the integrator is working in. Only withdrawing does, and
    `withdraw` refuses while anything is submitted on top.
    """
    if not item.application_id:
        return []
    return [
        review
        for review in _reviews(_prerequisite_applications(item.application))
        if review.status == ReviewItem.Status.DRAFT
    ]


def pending_dependants(item):
    """Open reviews built on this one, directly or not, earliest first.

    None of them can be decided before this review is approved, and it cannot be
    withdrawn while they wait on it.
    """
    if not item.application_id:
        return []
    return [
        review
        for review in _reviews(_dependant_applications(item.application))
        if review.pending
    ]


def pending_prerequisites(item):
    """What must be approved before this review can be decided.

    A decision keeps the order: the program's own prerequisites, such as a
    verified organisation, then every unapproved application this one builds
    on, earliest first. `waiting_reviews` is the same test as a filter.
    """
    pending = list(item.definition.pending_prerequisites(item))
    if item.application_id:
        pending.extend(
            Prerequisite(application.title, getattr(application, "review_item", None))
            for application in _prerequisite_applications(item.application)
            if application.status
            not in registry.get(application.application_type).success_statuses
        )
    return pending


def waiting_reviews():
    """Open reviews that cannot be decided yet: `pending_prerequisites` as a filter.

    The queue sorts ready reviews from waiting ones in the database. A join per
    level reaches as far back as the longest chain in any catalog, since
    dependencies only ever follow a catalog's predecessors.
    """
    unsettled = Q(pk__in=[])
    for definition in registry.all():
        unsettled |= Q(application_type=definition.key) & ~Q(
            status__in=definition.success_statuses,
        )
    held = Q(pk__in=[])
    for form in registry.forms():
        due = form.prerequisites_due()
        if due is not None:
            held |= Q(form__form_key=form.key) & due
    depth = max((program.longest_chain() for program in registry.programs()), default=0)
    for level in range(1, depth + 1):
        lookup = "__".join(["dependent_applications"] * level)
        held |= Exists(
            ApplicationInstance.objects.filter(
                unsettled,
                **{lookup: OuterRef("application")},
            ),
        )
    return Q(status__in=PENDING_STATUSES) & held


def prerequisite_names(prerequisites):
    """ "M1 - ABHA and identity is" / "M1 and organisation verification are"."""
    verb = "is" if len(prerequisites) == 1 else "are"
    return f"{readable_list(item.name for item in prerequisites)} {verb}"


def can_edit_review(item):
    return item.editable or (
        item.status == ReviewItem.Status.APPROVED
        and item.definition.allow_approved_updates
    )


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
    removed_under_review = [
        program.milestones[key].code
        for key in product.milestones.filter(
            application__review_item__status__in=["new", "in_review", "query_raised"],
        )
        .exclude(key__in=new_keys)
        .values_list("key", flat=True)
    ]
    if removed_under_review:
        msg = (
            f"{readable_list(removed_under_review)} "
            f"{'is' if len(removed_under_review) == 1 else 'are'} under review. "
            "Withdraw the request before removing the milestone."
        )
        raise ValidationError(msg)
    for key, value in product_values.items():
        setattr(product, key, value)
    product.save(update_fields=["name", "description", "product_type", "updated_at"])
    workspace.solution_type = solution_type
    workspace.applied_milestones = selections
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


def _require_editable_submission(item, *, submit):
    if not can_edit_review(item):
        msg = "Withdraw this request before editing the submitted form."
        raise ValidationError(msg)
    if (
        item.status == ReviewItem.Status.APPROVED
        and item.definition.auto_approve
        and not submit
    ):
        msg = "Submit your updated answers to keep participation recorded."
        raise ValidationError(msg)


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
    _require_editable_submission(item, submit=submit)
    if expected_revision is not None and str(item.selected_submission_id or "") != str(
        expected_revision,
    ):
        msg = "A teammate updated this form. Reload the page before saving."
        raise ValidationError(
            msg,
        )
    reason = milestone_unavailable(item)
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
    if item.status == ReviewItem.Status.APPROVED:
        _record_released(item.organisation)
    return item, form, True


@transaction.atomic
def certification_review(product, actor):
    """Continue an open certification request, or begin a new review cycle."""
    require_integrator(actor, product.organisation)
    Organisation.objects.select_for_update().get(pk=product.organisation_id)
    definition = product.workspace.definition.applications.certification
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
    definition = program.applications.product
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
    # Recording the registration stamped the workspace behind this instance.
    workspace.refresh_from_db()
    return workspace, form


@transaction.atomic
def withdraw(item, actor):
    item = _lock_review(item.pk)
    require_integrator(actor, item.organisation)
    if not item.pending:
        msg = "Only an active review request can be withdrawn."
        raise ValidationError(msg)
    dependants = pending_dependants(item)
    if dependants:
        # Latest first, the order they can be withdrawn in.
        names = readable_list(review.title for review in reversed(dependants))
        builds = "It builds" if len(dependants) == 1 else "They build"
        msg = f"Withdraw {names} first. {builds} on this request."
        raise ValidationError(msg)
    item.status = ReviewItem.Status.DRAFT
    item.save(update_fields=["status"])
    _set_application_status(item, "draft")
    item.definition.on_withdraw(item, actor)
    audit(actor=actor, action="Request withdrawn", item=item)
    _notice(item, "withdrawn")


@transaction.atomic
def reuse_evidence(item, actor):
    item = _lock_review(item.pk)
    require_integrator(actor, item.organisation)
    if (
        not item.definition.allow_reuse
        or not item.editable
        or milestone_unavailable(item)
    ):
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
        _notice(item, "assigned")


def _approve_subject(item, actor):
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
    _notice(item, "recorded")


def _record_released(organisation):
    """Record the waiting requests that no longer wait on anything.

    A request nobody decides can still have prerequisites, as UHI waits on M1 and
    on its organisation's verification. Any approval in the organisation may be
    the last one it needed, so each approval checks them all again. Call this
    once the approval is saved: recording is itself an approval, and must not
    find its own request still pending.
    """
    recorded = True
    while recorded:
        recorded = False
        waiting = ReviewItem.objects.filter(
            organisation=organisation,
            status__in=[
                ReviewItem.Status.NEW,
                ReviewItem.Status.IN_REVIEW,
                ReviewItem.Status.QUERY,
            ],
        ).select_related("selected_submission", "form", "product", "application")
        for item in waiting:
            if item.definition.auto_approve and not pending_prerequisites(item):
                _auto_approve(item, None)
                item.save()
                audit(actor=None, action="Recorded", item=item)
                recorded = True


def _require_decidable(item, action):
    if not item.pending:
        msg = "This item is not awaiting a decision."
        raise ValidationError(msg)
    if item.definition.auto_approve:
        msg = "This request is recorded once its prerequisites are approved."
        raise ValidationError(msg)
    if action not in {"approve", "send_back", "query"}:
        msg = "Choose a valid review action."
        raise ValidationError(msg)
    # A query can be raised at any time; the decision waits for what came first.
    prerequisites = pending_prerequisites(item) if action != "query" else []
    if prerequisites:
        msg = (
            "Approve or send back this request once "
            f"{prerequisite_names(prerequisites)} approved."
        )
        raise ValidationError(msg)


@transaction.atomic
def decide(item, actor, *, action, note="", field_key="form"):
    item = _lock_review(item.pk)
    require_decider(actor, item, "write" if action == "query" else "approve")
    _require_decidable(item, action)
    note = note.strip()
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
    if action == "approve":
        _record_released(item.organisation)
    _announce(item, action, note)
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
    _notice(item, "query_answered", note=body.strip())


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
