from datetime import timedelta
from pathlib import Path
from statistics import median

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.db.models import F
from django.db.models import Q
from django.db.models.functions import Upper
from django.http import FileResponse
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences.definitions import DocumentReadError
from ohc_experience.experiences.definitions import Prerequisite
from ohc_experience.experiences.definitions import readable_list
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.integrations.selectors import awaiting_provisioning
from ohc_experience.integrations.selectors import provisioning_can_be_retried
from ohc_experience.integrations.selectors import provisioning_progress
from ohc_experience.integrations.services import start_provisioning
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply

from . import credentials as credential_services
from . import permissions
from . import production as production_services
from . import workflows as services
from .context_processors import navigation_context
from .context_processors import product_scope
from .context_processors import selected_workspace
from .context_processors import workspaces_for
from .forms import CallbackURLForm
from .forms import SupportForm
from .models import AuditEvent
from .models import EventRegistration
from .models import Notification
from .models import ProductCredential
from .models import ReviewItem
from .models import ReviewQuery
from .models import SubmissionStatus
from .models import TicketAttachment
from .presentation import agent_skill_groups
from .presentation import default_agent_skill
from .presentation import overview_next_step
from .presentation import overview_progress
from .queue_presentation import grouped_requests
from .queue_presentation import populate_queue_page
from .queue_presentation import queue_requests
from .queue_presentation import review_order
from .registry import get_program
from .support_presentation import support_inbox

# Reading a document costs an implementation a paid call to somebody else.
DOCUMENT_READ_WINDOW_SECONDS = 3600


def _document_read_allowed(user):
    key = f"document-read:{user.pk}"
    if cache.get(key, 0) >= settings.EXPERIENCE_DOCUMENT_READ_HOURLY_LIMIT:
        return False
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=DOCUMENT_READ_WINDOW_SECONDS)
    return True


def _read_document(request, form_definition):
    """Answer a form's document hook as JSON; the caller has already authorised.

    Nothing is stored and nothing is trusted. The proposed values travel back
    through the form's own validation when the draft is saved.
    """
    field_key = request.POST.get("field", "")
    upload = request.FILES.get(field_key)
    field = form_definition.form_class.base_fields.get(field_key)
    if not isinstance(field, forms.FileField):
        return JsonResponse(
            {"error": "That field does not accept a document."},
            status=400,
        )
    if upload is None:
        return JsonResponse({"error": "Choose a document to read."}, status=400)
    try:
        # The form's own validators decide what this field accepts.
        field.clean(upload, None)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=400)
    if not _document_read_allowed(request.user):
        return JsonResponse(
            {
                "error": (
                    "Too many documents read in the last hour. "
                    "Enter the details yourself."
                ),
            },
            status=429,
        )
    try:
        fields = form_definition.read_document(field_key, upload)
    except DocumentReadError as error:
        return JsonResponse(
            {"error": str(error), "retryable": error.retryable},
            status=503 if error.retryable else 422,
        )
    return JsonResponse({"name": upload.name, "fields": fields})


def _organisation(request):
    membership = get_membership_for(request.user)
    if not membership:
        msg = "An integrator account with an organisation is required."
        raise PermissionDenied(
            msg,
        )
    return membership.organisation


def _workspaces(user):
    return workspaces_for(user)


def _workspace(request, reference):
    workspace = get_object_or_404(_workspaces(request.user), reference=reference)
    request.session["experience_product"] = workspace.reference
    return workspace


def _item(request, pk):
    query = permissions.visible_reviews(request.user).select_related(
        "selected_submission",
        "form",
        "organisation",
        "product__workspace",
        "application",
        "assignee",
        "decided_by",
    )
    if not permissions.reviewer(request.user):
        query = query.filter(organisation__memberships__user=request.user)
    return get_object_or_404(query, pk=pk)


def _tracks(workspace, user):
    rows = {
        milestone.key: milestone
        for milestone in workspace.product.milestones.filter(
            enabled=True,
        ).select_related("application__review_item")
    }
    applied = {
        track.code: workspace.definition.applied_keys(
            track,
            workspace.applied_milestones,
        )
        for track in workspace.definition.tracks
    }
    result = []
    for track in permissions.allowed_tracks(user, workspace.definition):
        tiles = []
        for key in applied[track.code]:
            if key not in rows:
                continue
            milestone = rows[key]
            definition = workspace.definition.milestones[key]
            # Only tracks this product actually applied for: naming one it never
            # chose is noise. The registration note carries the catalogue-wide fact.
            shared = [
                code
                for code in workspace.definition.shared_with(key, track.code)
                if key in applied[code]
            ]
            item = milestone.application.review_item
            label = (
                "Open"
                if item.status == "draft" and not item.selected_submission_id
                else item.get_status_display()
            )
            tiles.append(
                {
                    "milestone": milestone,
                    "item": item,
                    "definition": definition,
                    "status": item.status,
                    "label": label,
                    "shared_label": f"Shared with {readable_list(shared)}"
                    if shared
                    else "",
                    "reply_needed": item.status == "query_raised"
                    and item.queries.filter(
                        submission_id=item.selected_submission_id,
                        status="open",
                    ).exists(),
                    "url": reverse(
                        "experiences:track",
                        args=[workspace.reference, track.code],
                    )
                    + f"?milestone={key}",
                },
            )
        codes = {tile["definition"].key: tile["definition"].code for tile in tiles}
        for tile in tiles:
            tile["needs"] = codes.get(tile["definition"].predecessor, "")
        result.append(
            {
                "definition": track,
                "shared_note": workspace.definition.shared_note(track.code),
                "tiles": tiles,
                "approved": sum(tile["status"] == "approved" for tile in tiles),
            },
        )
    return result


def _lock_tiles(workspace, tracks):
    """Name what each unsubmitted milestone waits on before its form opens."""
    codes = {
        milestone.application_id: workspace.definition.milestones[milestone.key].code
        for milestone in workspace.product.milestones.all()
    }
    locked_by = {}
    for tile in (tile for track in tracks for tile in track["tiles"]):
        key = tile["definition"].key
        if key not in locked_by:
            unsubmitted = (
                services.unsubmitted_prerequisites(tile["item"])
                if tile["item"].editable
                else []
            )
            locked_by[key] = readable_list(
                codes.get(review.application_id, review.title) for review in unsubmitted
            )
        tile["locked_by"] = locked_by[key]
        if tile["locked_by"]:
            tile["label"] = "Locked"


def _page(request, items):
    return Paginator(items, 10).get_page(request.GET.get("page"))


def _context(request, workspace=None, **kwargs):
    is_reviewer = permissions.reviewer(request.user)
    if is_reviewer:
        # Staff never work inside a product: the switcher and the product's own
        # navigation belong to its integrators. Staff pages link to products instead.
        workspace = None
    elif workspace is None:
        workspace = selected_workspace(request)
    request.experience_navigation = navigation_context(request, workspace)
    result = {
        **request.experience_navigation,
        "experience_program": workspace.definition if workspace else get_program(),
        "reviewer": is_reviewer,
        "workspaces": _workspaces(request.user),
        "workspace": workspace,
        "tracks": _tracks(workspace, request.user) if workspace else [],
        "today": timezone.localdate(),
        **kwargs,
    }
    item = kwargs.get("item")
    if item:
        result["submission_blocked"] = item.definition.submission_block_reason(item)
        if "prerequisites" not in result:
            result["prerequisites"] = (
                services.pending_prerequisites(item) if item.pending else []
            )
        result["waiting_on"] = (
            services.prerequisite_names(result["prerequisites"])
            if result["prerequisites"]
            else ""
        )
        result["withdrawn_hold"] = services.withdrawn_hold(
            item,
            result["prerequisites"],
        )
        if item.pending and not is_reviewer:
            # Latest first, the order they can be withdrawn in.
            result["withdraw_first"] = [
                (review, _integrator_item_url(review))
                for review in reversed(services.pending_dependants(item))
            ]
    if workspace:
        result["organisation"] = workspace.product.organisation
        result["can_integrate"] = permissions.can_integrate(
            request.user,
            workspace.product.organisation,
        )
    if not is_reviewer:
        org = result.get("organisation") or _organisation(request)
        result["organisation"] = org
        result["can_integrate"] = permissions.can_integrate(request.user, org)
    return result


def _error(request, error):
    messages.error(request, " ".join(error.messages))


def _certification_context(request, product):
    program = product.workspace.definition
    if permissions.reviewer(request.user) and not permissions.has_access(
        request.user,
        "review",
        program=program.key,
    ):
        return {}
    context = program.certification_context(product)
    certificate = context.get("certificate")
    if (
        certificate
        and not permissions.visible_submissions(request.user)
        .filter(
            pk=certificate.submission_id,
        )
        .exists()
    ):
        context = {**context, "certificate": None}
    return context


@login_required
def dashboard(request):
    if permissions.reviewer(request.user):
        return redirect(permissions.staff_home(request.user))
    organisation = _organisation(request)
    if not organisation.is_onboarded:
        return redirect("experiences:organisation")
    workspace = (
        _workspaces(request.user)
        .filter(
            reference=request.GET.get(
                "product",
                request.session.get("experience_product", ""),
            ),
        )
        .first()
        or _workspaces(request.user).order_by("product__created_at").first()
    )
    return redirect(
        workspace.get_absolute_url() if workspace else "experiences:product-create",
    )


@login_required
def products(request):
    permissions.require_area(request.user, "review")
    if permissions.reviewer(request.user):
        return _reviewer_products(request)
    return render(
        request,
        "experiences/products.html",
        _context(
            request,
            page_title="Products",
            nav="products",
            products=_page(request, _workspaces(request.user)),
        ),
    )


def _require_reviewer_area(user):
    if not permissions.reviewer(user):
        msg = "Only authorised reviewers can access organisations."
        raise PermissionDenied(msg)
    permissions.require_area(user, "review")


def _open_requests(user):
    return _review_requests(user).filter(status__in=services.PENDING_STATUSES)


def _product_rows(user):
    """The products a reviewer can see, each with its open request count."""
    return _workspaces(user).annotate(
        open_count=Count(
            "product__review_items",
            filter=Q(product__review_items__in=_open_requests(user)),
            distinct=True,
        ),
    )


def _solution_type_choices(user):
    """The solution types the reviewer's products applied for, in catalogue order."""
    applied = {
        key
        for keys in _workspaces(user)
        .order_by()
        .values_list("solution_type", flat=True)
        .distinct()
        for key in keys
    }
    return [
        (key, label)
        for key, label in get_program().solution_types.items()
        if key in applied
    ]


def _state_label(state):
    """LGD names states in capitals: "JAMMU AND KASHMIR" reads "Jammu and Kashmir"."""
    return " ".join(
        word.lower() if word == "And" else word for word in state.title().split()
    )


def _organization_filter_choices(organizations):
    """Entity types and states the reviewer's organizations hold, and nothing else."""
    held = set(organizations.order_by().values_list("entity_type", flat=True))
    states = (
        organizations.exclude(state="")
        .order_by()
        .values_list(Upper("state"), flat=True)
        .distinct()
    )
    return (
        [
            (value, label)
            for value, label in get_program().signup_organisation_choices
            if value in held
        ],
        [(state, _state_label(state)) for state in sorted(states)],
    )


@login_required
def organizations(request):
    _require_reviewer_area(request.user)
    visible = permissions.visible_organisations(request.user)
    rows = visible
    search = request.GET.get("q", "").strip()
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(legal_name__icontains=search))
    status = request.GET.get("status", "")
    if status in Organisation.VerificationStatus.values:
        rows = rows.filter(verification_status=status)
    else:
        status = ""
    entity_type_choices, state_choices = _organization_filter_choices(visible)
    entity_type = request.GET.get("entity_type", "")
    if entity_type in dict(entity_type_choices):
        rows = rows.filter(entity_type=entity_type)
    else:
        entity_type = ""
    state = request.GET.get("state", "")
    if state in dict(state_choices):
        rows = rows.filter(state__iexact=state)
    else:
        state = ""
    rows = rows.annotate(
        visible_product_count=Count(
            "products",
            filter=Q(products__in=permissions.visible_products(request.user)),
            distinct=True,
        ),
        open_count=Count(
            "review_items",
            filter=Q(review_items__in=_open_requests(request.user)),
            distinct=True,
        ),
    ).order_by("name")
    return render(
        request,
        "experiences/organizations.html",
        _context(
            request,
            page_title="Organisations",
            nav="organizations",
            organizations=_page(request, rows),
            search=search,
            status_choices=Organisation.VerificationStatus.choices,
            selected_status=status,
            entity_type_choices=entity_type_choices,
            selected_entity_type=entity_type,
            state_choices=state_choices,
            selected_state=state,
            filtered=bool(search or status or entity_type or state),
        ),
    )


def _reviewer_products(request):
    rows = _product_rows(request.user)
    organization_choices = (
        permissions.visible_organisations(request.user)
        .filter(pk__in=_workspaces(request.user).values("product__organisation"))
        .order_by("name")
    )
    organization = request.GET.get("organization", "")
    if organization and organization_choices.filter(slug=organization).exists():
        rows = rows.filter(product__organisation__slug=organization)
    else:
        organization = ""
    solution_type_choices = _solution_type_choices(request.user)
    solution_type = request.GET.get("solution_type", "")
    if solution_type in dict(solution_type_choices):
        rows = rows.filter(solution_type__contains=[solution_type])
    else:
        solution_type = ""
    search = request.GET.get("q", "").strip()
    if search:
        rows = rows.filter(
            Q(product__name__icontains=search) | Q(reference__icontains=search),
        )
    return render(
        request,
        "experiences/reviewer_products.html",
        _context(
            request,
            page_title="Products",
            nav="organizations",
            products=_page(request, rows),
            organization_choices=organization_choices,
            selected_organization=organization,
            solution_type_choices=solution_type_choices,
            selected_solution_type=solution_type,
            search=search,
        ),
    )


@login_required
def organization_detail(request, slug):
    _require_reviewer_area(request.user)
    organization = get_object_or_404(
        permissions.visible_organisations(request.user),
        slug=slug,
    )
    visible_reviews = (
        _review_requests(request.user)
        .filter(organisation=organization)
        .select_related(
            "assignee",
            "application",
            "product__workspace",
            "selected_submission",
        )
        .order_by("-submitted_at", "-pk")
    )
    products = (
        _product_rows(request.user)
        .filter(product__organisation=organization)
        .order_by("-open_count", "product__name")
    )
    verification = visible_reviews.filter(kind=ReviewItem.Kind.ORGANISATION).first()
    withdrawn = (
        verification
        and organization.verification_status
        == Organisation.VerificationStatus.WITHDRAWN
    )
    return render(
        request,
        "experiences/organization_detail.html",
        _context(
            request,
            page_title=organization.display_name,
            nav="organizations",
            organization=organization,
            organization_details=(
                verification.selected_submission.data
                if verification and verification.selected_submission
                else {}
            ),
            withdrawn_verification=verification if withdrawn else None,
            withdrawn_at=services.withdrawn_at(verification) if withdrawn else None,
            products=products,
            # A draft is the integrator's unsent work, not yet a request.
            review_requests=_page(request, visible_reviews.exclude(status="draft")),
        ),
    )


def _product_review_id(value):
    if (
        not value.isascii()
        or not value.isdecimal()
        or len(value) > 19  # noqa: PLR2004
        or not 0 < int(value) < 2**63
    ):
        msg = "Reload the product page before reviewing these submissions."
        raise ValidationError(msg)
    return int(value)


def _posted_product_reviews(request):
    revisions = {}
    for value in request.POST.getlist("reviews"):
        parts = value.split(":")
        if (
            len(parts) != 2  # noqa: PLR2004
            or _product_review_id(parts[0]) in revisions
        ):
            msg = "Reload the product page before reviewing these submissions."
            raise ValidationError(msg)
        revisions[_product_review_id(parts[0])] = _product_review_id(parts[1])
    return revisions


def _product_review_scope(product):
    return Q(product=product) | Q(
        organisation=product.organisation,
        kind=ReviewItem.Kind.ORGANISATION,
        product__isnull=True,
        application__isnull=True,
    )


def _product_review_post(request, workspace):
    if request.POST.get("intent") == "bulk_decision":
        decided = services.decide_product(
            workspace.product,
            request.user,
            action=request.POST.get("action"),
            expected_revisions=_posted_product_reviews(request),
            note=request.POST.get("note", ""),
        )
        verb = "Approved" if request.POST.get("action") == "approve" else "Rejected"
        noun = "request" if len(decided) == 1 else "requests"
        messages.success(
            request,
            f"{verb} {len(decided)} submitted {noun}.",
        )
        anchor = "decisions"
    elif request.POST.get("intent") == "decision":
        review_id = _product_review_id(request.POST.get("review_id", ""))
        item = get_object_or_404(
            permissions.visible_reviews(request.user).filter(
                _product_review_scope(workspace.product),
            ),
            pk=review_id,
        )
        services.decide(
            item,
            request.user,
            action=request.POST.get("action"),
            note=request.POST.get("note", ""),
            reason=request.POST.get("reason", ""),
            field_key=request.POST.get("field_key", "form"),
            expected_revision=request.POST.get("revision", ""),
        )
        messages.success(request, "Review updated.")
        anchor = f"review-{item.pk}"
    else:
        msg = "Choose a review action."
        raise ValidationError(msg)
    return redirect(
        reverse("experiences:product-detail", args=[workspace.reference])
        + f"#{anchor}",
    )


def _product_review_sections(request, items):
    sections = []
    for item in items:
        snapshot = item.selected_submission
        if item.status == ReviewItem.Status.DRAFT or not (
            snapshot and snapshot.status == SubmissionStatus.COMPLETED
        ):
            snapshot = None
        prerequisites = services.pending_prerequisites(item) if item.pending else []
        unresolved = sum(
            query.submission_id == item.selected_submission_id
            and query.status != "resolved"
            for query in item.queries.all()
        )
        actions = permissions.available_review_actions(request.user, item)
        can_approve = "approve" in actions and not prerequisites and not unresolved
        can_reject = "reject" in actions and not prerequisites
        can_query = "query" in actions
        can_override = "approve" in actions and bool(
            services.overridden_prerequisites(item),
        )
        available = [
            action
            for action, allowed in (
                ("approve", can_approve),
                ("reject", can_reject),
                ("query", can_query),
            )
            if allowed
        ]
        posted = request.POST.get("review_id") == str(item.pk)
        action = request.POST.get("action") if posted else None
        sections.append(
            {
                "item": item,
                "snapshot": snapshot,
                "prerequisites": prerequisites,
                "waiting_on": services.prerequisite_names(prerequisites)
                if prerequisites
                else "",
                "withdrawn_hold": services.withdrawn_hold(item, prerequisites),
                "withdrawn_at": services.withdrawn_at(item)
                if item.status == ReviewItem.Status.DRAFT
                else None,
                "can_approve": can_approve,
                "can_reject": can_reject,
                "can_query": can_query,
                "can_override": can_override,
                "available_actions": available,
                "unresolved_query_count": unresolved,
                "decision_note": request.POST.get("note", "") if posted else "",
                "reject_reasons": services.reject_reasons(item.definition),
                "other_reason": services.OTHER_REASON,
                "decision_reason": request.POST.get("reason", "") if posted else "",
                "decision_action": action
                if action in available
                else next(iter(available), ""),
                "query_field": request.POST.get("field_key", "form")
                if posted
                else "form",
            },
        )
    return sections


def _review_groups(sections):
    """Open requests outside the tracks on top, then milestones, approved last."""
    groups = {"outside_tracks": [], "pending": [], "rejected": [], "approved": []}
    for section in sections:
        item = section["item"]
        if item.status == ReviewItem.Status.APPROVED:
            groups["approved"].append(section)
        elif not getattr(item.application, "milestone", None):
            groups["outside_tracks"].append(section)
        elif item.status == ReviewItem.Status.REJECTED:
            groups["rejected"].append(section)
        else:
            groups["pending"].append(section)
    return groups


def _bulk_holds(approve_blockers, reject_blockers):
    """Group hold reasons by the buttons they hold, so a shared one is said once."""
    shared = [reason for reason in approve_blockers if reason in reject_blockers]
    groups = (
        ("Accept all and Reject all are", shared),
        ("Accept all is", [r for r in approve_blockers if r not in shared]),
        ("Reject all is", [r for r in reject_blockers if r not in shared]),
    )
    return [
        {"label": label, "reasons": reasons} for label, reasons in groups if reasons
    ]


@login_required
@require_http_methods(["GET", "POST"])
def product_detail(request, reference):
    """Review a product's submitted evidence and milestone progress together."""
    _reviewer_required(request)
    workspace = get_object_or_404(_workspaces(request.user), reference=reference)
    product = workspace.product
    program = workspace.definition
    if request.method == "POST":
        if request.POST.get("intent") == "retry_provisioning":
            if not _can_provision(request.user, product, program.key):
                raise PermissionDenied
            _provision(request, product)
            return redirect(
                reverse("experiences:product-detail", args=[workspace.reference])
                + "#connection",
            )
        try:
            return _product_review_post(request, workspace)
        except ValidationError as error:
            _error(request, error)
    visible_items = permissions.visible_reviews(request.user)
    product_items = sorted(
        visible_items.filter(_product_review_scope(product))
        .exclude(kind=ReviewItem.Kind.PRODUCT)
        # A draft is the integrator's unsent work. A withdrawn organisation
        # verification stays in view, read only, since every milestone waits on it.
        .filter(
            ~Q(status=ReviewItem.Status.DRAFT)
            | Q(
                kind=ReviewItem.Kind.ORGANISATION,
                organisation__verification_status=(
                    Organisation.VerificationStatus.WITHDRAWN
                ),
            ),
        )
        .select_related(
            "selected_submission__form",
            "form",
            "organisation",
            "product__workspace",
            "application__milestone",
            "assignee",
        )
        .prefetch_related("queries__raised_by", "queries__replied_by")
        .order_by("pk"),
        key=review_order,
    )
    review_sections = _product_review_sections(request, product_items)
    bulk_items = [
        item
        for item in product_items
        if item.pending
        and item.selected_submission_id
        and item.selected_submission.status == SubmissionStatus.COMPLETED
        and item.submitted_at
        and not item.definition.auto_approve
        and permissions.can_review(request.user, item, "approve")
    ]
    bulk_approve_blockers = (
        services.product_decision_blockers(bulk_items, request.user, action="approve")
        if bulk_items
        else []
    )
    bulk_reject_blockers = (
        services.product_decision_blockers(bulk_items, request.user, action="reject")
        if bulk_items
        else []
    )
    pending = list(
        visible_items.filter(
            product_scope(workspace),
            organisation=product.organisation,
            status__in=["new", "in_review", "query_raised"],
        ).select_related(
            "application__milestone__product__workspace",
            "assignee",
            "product",
        ),
    )
    decidable = {
        pk
        for action in ("write", "approve")
        for pk in permissions.visible_reviews(request.user, action)
        .filter(pk__in=[item.pk for item in pending])
        .values_list("pk", flat=True)
    }
    tracks = _tracks(workspace, request.user)
    progress = overview_progress(tracks)
    submitted_ids = {item.pk for item in product_items}
    for row in tracks:
        row["tiles"] = [
            tile for tile in row["tiles"] if tile["item"].pk in submitted_ids
        ]
        for tile in row["tiles"]:
            tile["url"] = f"#review-{tile['item'].pk}"
    general_access = permissions.has_access(request.user, "review", program=program.key)
    organisation_section = next(
        (
            section
            for section in review_sections
            if section["item"].kind == ReviewItem.Kind.ORGANISATION
        ),
        None,
    )
    certification = _certification_context(request, product)
    activity = AuditEvent.objects.filter(
        Q(product=product) | Q(item_id=organisation_section["item"].pk)
        if organisation_section
        else Q(product=product),
        item__in=visible_items,
    )
    outcomes = product.outcomes.filter(
        source_application__in=visible_items.values("application_id"),
    )
    if certification.get("current"):
        outcomes = outcomes.exclude(outcome_type=certification["current"].outcome_type)
    return render(
        request,
        "experiences/product_detail.html",
        _context(
            request,
            page_title=product.name,
            nav="organizations",
            experience_program=program,
            product=product,
            reference=workspace.reference,
            organisation=product.organisation,
            organisation_details=(
                organisation_section["snapshot"].data
                if organisation_section and organisation_section["snapshot"]
                else {}
            ),
            pending=pending,
            organisation_pending=[item for item in pending if not item.product_id],
            organisation_review=organisation_section["item"]
            if organisation_section
            else None,
            decidable=decidable,
            review_sections=review_sections,
            review_groups=_review_groups(review_sections),
            bulk_items=bulk_items,
            bulk_approve_blockers=bulk_approve_blockers,
            bulk_reject_blockers=bulk_reject_blockers,
            bulk_holds=_bulk_holds(bulk_approve_blockers, bulk_reject_blockers),
            bulk_note=request.POST.get("note", "")
            if request.POST.get("intent") == "bulk_decision"
            else "",
            min_note_length=services.MIN_REVIEW_TEXT,
            tracks=[row for row in tracks if row["tiles"]],
            progress=progress,
            registration=visible_items.filter(
                product=product,
                kind="product_registration",
            ).first(),
            certification=certification,
            credential=ProductCredential.objects.filter(product=product).first()
            if general_access
            else None,
            production=production_services.state(product)
            if production_services.can_view(request.user, program)
            else None,
            provisioning=provisioning_progress(product) if general_access else [],
            can_retry_provisioning=_can_provision(request.user, product, program.key),
            provisioning_never_started=awaiting_provisioning(product),
            general_access=general_access,
            open_tickets=permissions.visible_tickets(request.user).filter(
                product=product,
                status__in=["open", "awaiting_integrator"],
            )[:5],
            activity=activity.select_related("actor", "item")[:10],
            outcomes=outcomes.exclude(
                outcome_type=program.sandbox_credentials.outcome_type
                if program.sandbox_credentials
                else "",
            )[:6],
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def organisation(request):
    org = _organisation(request)
    permissions.require_integrator(request.user, org)
    item = services.organisation_review(org, request.user)
    form = services.build_form(item)
    if request.method == "POST":
        try:
            item, form, saved = services.save_review_form(
                item,
                request.user,
                data=request.POST,
                files=request.FILES,
                submit=True,
                expected_revision=request.POST.get("revision", ""),
            )
            if saved:
                messages.success(
                    request,
                    f"{capfirst(item.organisation.noun)} submitted for verification. "
                    "You can register your product while it is reviewed.",
                )
                return redirect(
                    "experiences:product-create"
                    if not org.products.exists()
                    else "experiences:organisation",
                )
        except ValidationError as error:
            _error(request, error)
    return render(
        request,
        "experiences/organisation.html",
        _context(
            request,
            item=item,
            form=form,
            page_title=f"{capfirst(item.organisation.noun)} details",
            onboarding=not org.products.exists(),
            nav="organisation",
            can_edit=services.can_edit_review(item),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def product_create(request):
    org = _organisation(request)
    permissions.require_integrator(request.user, org)
    if not org.is_onboarded:
        return redirect("experiences:organisation")
    form = (
        get_program()
        .applications.product.forms[0]
        .form_class(
            data=request.POST if request.method == "POST" else None,
        )
    )
    if request.method == "POST":
        workspace, form = services.register_product(
            org,
            request.user,
            data=request.POST,
        )
        if workspace:
            messages.success(request, "Product registered.")
            return redirect(workspace)
    return render(
        request,
        "experiences/product_form.html",
        _context(
            request,
            form=form,
            page_title="Register a product",
            onboarding=not org.products.exists(),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def product_edit(request, reference):
    workspace = _workspace(request, reference)
    permissions.require_integrator(request.user, workspace.product.organisation)
    item = workspace.product.review_items.get(kind="product_registration")
    form = services.build_form(item)
    if request.method == "POST":
        try:
            item, form, saved = services.save_review_form(
                item,
                request.user,
                data=request.POST,
                submit=True,
                expected_revision=request.POST.get("revision", ""),
            )
            if saved:
                messages.success(request, "Product updated.")
                return redirect(workspace)
        except ValidationError as error:
            _error(request, error)
    approved_selections = [
        value
        for value in workspace.applied_milestones
        if workspace.product.milestones.filter(
            key=value.split(":", 1)[1],
            application__status="approved",
        ).exists()
    ]
    under_review_selections = [
        value
        for value in workspace.applied_milestones
        if workspace.product.milestones.filter(
            key=value.split(":", 1)[1],
            application__review_item__status__in=services.PENDING_STATUSES,
        ).exists()
    ]
    return render(
        request,
        "experiences/product_form.html",
        _context(
            request,
            workspace,
            item=item,
            form=form,
            page_title="Edit product",
            nav="edit",
            can_edit=services.can_edit_review(item),
            approved_selections=approved_selections,
            under_review_selections=under_review_selections,
        ),
    )


@login_required
def overview(request, reference):
    if permissions.reviewer(request.user):
        return redirect("experiences:product-detail", reference=reference)
    workspace = _workspace(request, reference)
    product = workspace.product
    visible_items = permissions.visible_reviews(request.user)
    activity = product.audit_events.all()
    outcomes = product.outcomes.all()
    organisation_review = (
        visible_items.filter(
            organisation=product.organisation,
            kind="organisation_verification",
        )
        .select_related("selected_submission")
        .first()
    )
    certification = _certification_context(request, product)
    if certification.get("current"):
        outcomes = outcomes.exclude(
            outcome_type=certification["current"].outcome_type,
        )
    context = _context(
        request,
        workspace,
        page_title="Overview",
        nav="overview",
        organisation_details=(
            organisation_review.selected_submission.data
            if organisation_review and organisation_review.selected_submission
            else {}
        ),
        activity=activity.select_related("actor", "item")[:10],
        events=permissions.visible_events(request.user).upcoming()[:3],
        registered_events=set(
            EventRegistration.objects.filter(
                user=request.user,
            ).values_list("event_id", flat=True),
        ),
        credential=ProductCredential.objects.filter(product=product).first(),
        production=production_services.state(product),
        outcomes=outcomes.exclude(
            outcome_type=workspace.definition.sandbox_credentials.outcome_type
            if workspace.definition.sandbox_credentials
            else "",
        )[:6],
        certification=certification,
    )
    _lock_tiles(workspace, context["tracks"])
    context["progress"] = overview_progress(context["tracks"])
    context["next_step"] = (
        overview_next_step(workspace, context["tracks"], organisation_review)
        if context["can_integrate"]
        else None
    )
    context["handoffs"] = []
    if context["can_integrate"]:
        for key, definition in workspace.definition.handoffs.items():
            options = [
                option
                for option in definition.options(product, actor=request.user)
                if option["enabled"]
            ]
            if options:
                context["handoffs"].append(
                    {"key": key, "definition": definition, "options": options},
                )
    return render(request, "experiences/overview.html", context)


@login_required
@never_cache
@require_POST
def product_handoff(request, reference, handoff_key):
    workspace = _workspace(request, reference)
    product = workspace.product
    permissions.require_integrator(request.user, product.organisation)
    definition = workspace.definition.handoffs.get(handoff_key)
    if definition is None:
        raise Http404
    option = request.POST.get("option", "")
    available = {row["key"] for row in definition.options(product, actor=request.user)}
    try:
        url = definition.create_url(product, option=option, actor=request.user)
    except ValidationError as error:
        _error(request, error)
        result = "blocked"
        response = redirect(workspace.get_absolute_url())
    else:
        result = "created"
        response = redirect(url)
    services.audit(
        actor=request.user,
        product=product,
        action=f"{definition.name} handoff {result}",
        detail={
            "handoff": handoff_key,
            "option": option if option in available else "",
            "result": result,
        },
    )
    response["Referrer-Policy"] = "no-referrer"
    return response


@login_required
@require_http_methods(["GET", "POST"])
def product_certification(request, reference):
    workspace = _workspace(request, reference)
    product = workspace.product
    permissions.require_integrator(request.user, product.organisation)
    definition = workspace.definition.applications.certification
    if definition is None:
        raise Http404
    reviews = product.review_items.filter(
        application__application_type=definition.key,
    ).order_by("-created_at", "-pk")

    def revision():
        latest = reviews.first()
        return (
            f"{latest.pk}:{latest.status}:{latest.selected_submission_id or ''}"
            if latest
            else ""
        )

    certification = _certification_context(request, product)
    item = certification.get("review")
    form = (
        services.build_form(item)
        if item
        else definition.forms[0].form_class(product=product)
    )
    if request.method == "POST":
        if request.POST.get("intent") == "read":
            return _read_document(request, definition.forms[0])
        try:
            intent = request.POST.get("intent")
            if intent not in {"draft", "submit"}:
                msg = "Choose a valid form action."
                raise ValidationError(msg)  # noqa: TRY301
            with transaction.atomic():
                type(product.organisation).objects.select_for_update().get(
                    pk=product.organisation_id,
                )
                if request.POST.get("certification_revision", "") != revision():
                    msg = (
                        "This WASA request has changed. Reload the page before saving."
                    )
                    raise ValidationError(msg)  # noqa: TRY301
                item = services.certification_review(product, request.user)
                item, form, saved = services.save_review_form(
                    item,
                    request.user,
                    data=request.POST,
                    files=request.FILES,
                    submit=intent == "submit",
                    expected_revision=request.POST.get("revision", ""),
                )
            if saved:
                messages.success(
                    request,
                    "WASA submitted for review."
                    if intent == "submit"
                    else "Draft saved.",
                )
                return redirect(
                    "experiences:product-certification",
                    workspace.reference,
                )
        except ValidationError as error:
            _error(request, error)
        certification = _certification_context(request, product)
    return render(
        request,
        "experiences/certification.html",
        _context(
            request,
            workspace,
            page_title="WASA certification",
            nav="certification",
            item=item,
            form=form,
            certification=certification,
            certification_revision=revision(),
            certification_reviews=reviews.select_related("selected_submission"),
            can_edit=not item or item.editable,
        ),
    )


def _staff_track(request, reference, track_code):
    """A track link opens the milestone's review for staff, or the product's tracks."""
    workspace = get_object_or_404(_workspaces(request.user), reference=reference)
    program = workspace.definition
    track = program.track_map().get(track_code)
    if track is None or not permissions.has_access(
        request.user,
        "review",
        track_code,
        program=program.key,
    ):
        raise Http404
    key = request.GET.get("milestone", "")
    item = (
        permissions.visible_reviews(request.user)
        .filter(
            product=workspace.product,
            application__milestone__key=key,
            application__milestone__enabled=True,
        )
        .first()
        if key in program.applied_keys(track, workspace.applied_milestones)
        else None
    )
    if item:
        return redirect(item)
    return redirect(
        reverse("experiences:product-detail", args=[workspace.reference])
        + f"#track-{slugify(track_code)}",
    )


def _additional_evidence_reviews(request):
    revisions = {}
    try:
        for value in request.POST.getlist("additional_reviews"):
            pk, revision = value.split(":")
            pk = _product_review_id(pk)
            if pk in revisions:
                raise ValueError  # noqa: TRY301
            revisions[pk] = _product_review_id(revision) if revision else ""
    except (ValueError, ValidationError) as error:
        msg = "Reload the milestone page before choosing submissions."
        raise ValidationError(msg) from error
    return revisions


def _milestone_testing_dates(request, *, prefix, errors=None):
    errors = errors or {}
    return [
        {
            "name": f"{prefix}{key}",
            "id": f"id_{prefix}{key}",
            "label": label,
            "value": request.POST.get(f"{prefix}{key}", ""),
            "errors": errors.get(key, []),
            "max": timezone.localdate().isoformat(),
            "is_start": key == "start_date",
        }
        for key, label in (
            ("start_date", "Testing start date"),
            ("end_date", "Testing end date"),
        )
    ]


def _track_evidence_context(request, item, form):
    if not item or not permissions.can_integrate(request.user, item.organisation):
        return {}
    choices = []
    additional_forms = getattr(form, "additional_forms", {})
    selected = set(request.POST.getlist("additional_reviews"))
    for target in sorted(services.submission_targets(item), key=review_order):
        milestone = target.program.milestones[target.application.milestone.key]
        token = f"{target.pk}:{target.selected_submission_id or ''}"
        choices.append(
            {
                "item": target,
                "code": milestone.code,
                "name": milestone.name,
                "token": token,
                "selected": token in selected,
                "dates": _milestone_testing_dates(
                    request,
                    prefix=f"milestone_{target.pk}_",
                    errors=additional_forms[target.pk].errors
                    if target.pk in additional_forms
                    else None,
                ),
                "requires": [
                    str(prerequisite.pk)
                    for prerequisite in services.unsubmitted_prerequisites(target)
                    if prerequisite.pk != item.pk
                ],
            },
        )
    return {
        "submission_choices": choices,
        "submission_track": next(
            (
                track.code
                for track in item.program.tracks
                if item.application.milestone.key in track.keys
            ),
            "",
        ),
    }


def _save_track_evidence(request, item):
    intent = request.POST.get("intent")
    additional = {}
    if intent in {"draft", "submit"}:
        if intent == "submit":
            additional = _additional_evidence_reviews(request)
        kwargs = {
            "data": request.POST,
            "files": request.FILES,
            "expected_revision": request.POST.get("revision", ""),
        }
        if additional:
            result = services.save_review_forms(
                item,
                request.user,
                additional_revisions=additional,
                **kwargs,
            )
        else:
            result = services.save_review_form(
                item,
                request.user,
                submit=intent == "submit",
                **kwargs,
            )
    else:
        msg = "Choose a valid form action."
        raise ValidationError(msg)
    notice = (
        f"Evidence submitted for {len(additional) + 1} milestones."
        if additional
        else item.definition.submitted_message
        if intent == "submit"
        else "Draft saved."
    )
    return *result, notice


@login_required
@require_http_methods(["GET", "POST"])
def track(request, reference, track_code):
    if permissions.reviewer(request.user):
        return _staff_track(request, reference, track_code)
    workspace = _workspace(request, reference)
    if track_code not in workspace.definition.track_map():
        raise Http404
    track_data = next(
        row
        for row in _tracks(workspace, request.user)
        if row["definition"].code == track_code
    )
    _lock_tiles(workspace, [track_data])
    selected = request.GET.get("milestone", "")
    default_tile = next(
        (
            tile
            for tile in track_data["tiles"]
            if tile["status"] != ReviewItem.Status.APPROVED
        ),
        next(iter(track_data["tiles"]), None),
    )
    tile = next(
        (tile for tile in track_data["tiles"] if tile["definition"].key == selected),
        default_tile,
    )
    item = tile["item"] if tile else None
    form = services.build_form(item) if item else None
    locked_by = (
        [
            (review, _integrator_item_url(review))
            for review in services.unsubmitted_prerequisites(item)
        ]
        if tile and tile["locked_by"]
        else []
    )
    if request.method == "POST":
        if item is None:
            raise Http404
        if request.POST.get("intent") == "read":
            return _read_document(request, item.definition)
        try:
            item, form, saved, notice = _save_track_evidence(request, item)
            if saved:
                messages.success(request, notice)
                return redirect(request.get_full_path())
        except ValidationError as error:
            _error(request, error)
    return render(
        request,
        "experiences/track.html",
        _context(
            request,
            workspace,
            page_title=track_code,
            nav=track_code,
            track=track_data,
            tile=tile,
            item=item,
            form=form,
            can_edit=services.can_edit_review(item) if item else False,
            locked_by=locked_by,
            **_track_evidence_context(request, item, form),
        ),
    )


def _integrator_item_url(item):
    if item.kind == "organisation_verification":
        return reverse("experiences:organisation")
    if item.kind == "product_registration":
        return reverse(
            "experiences:product-edit",
            args=[item.product.workspace.reference],
        )
    certification = item.program.applications.certification
    if certification and item.application.application_type == certification.key:
        return reverse(
            "experiences:product-certification",
            args=[item.product.workspace.reference],
        )
    key = item.application.milestone.key
    selection = next(
        value
        for value in item.product.workspace.applied_milestones
        if value.endswith(f":{key}")
    )
    return (
        reverse(
            "experiences:track",
            args=[item.product.workspace.reference, selection.split(":")[0]],
        )
        + f"?milestone={key}"
    )


@login_required
@require_POST
def withdraw(request, pk):
    item = _item(request, pk)
    try:
        services.withdraw(item, request.user)
        messages.success(request, "Request withdrawn. The form can now be edited.")
    except ValidationError as error:
        _error(request, error)
    return redirect(_integrator_item_url(item))


@login_required
@require_POST
def query_action(request, pk):
    query = get_object_or_404(ReviewQuery, pk=pk)
    item = _item(request, query.item_id)
    try:
        if request.POST.get("intent") == "resolve":
            services.resolve_query(query, request.user)
        else:
            services.reply_query(query, request.user, request.POST.get("body", ""))
        messages.success(request, "Query updated.")
    except ValidationError as error:
        _error(request, error)
    return_reference = request.POST.get("return_to_product")
    if return_reference and permissions.reviewer(request.user):
        if return_reference == "1" and item.product_id:
            return_reference = item.product.workspace.reference
        workspace = _workspaces(request.user).filter(reference=return_reference).first()
        if (
            workspace
            and permissions.visible_reviews(request.user)
            .filter(
                _product_review_scope(workspace.product),
                pk=item.pk,
            )
            .exists()
        ):
            return redirect(
                reverse(
                    "experiences:product-detail",
                    args=[workspace.reference],
                )
                + f"#review-{item.pk}",
            )
    return redirect(
        item.get_absolute_url()
        if permissions.reviewer(request.user)
        else _integrator_item_url(item),
    )


@login_required
def pending_queries(request):
    query = (
        permissions.visible_reviews(request.user)
        .filter(
            status__in=["query_raised", "in_review"],
        )
        .annotate(
            awaiting_reply_count=Count(
                "queries",
                filter=Q(
                    queries__submission_id=F("selected_submission_id"),
                    queries__status="open",
                ),
            ),
            unresolved_query_count=Count(
                "queries",
                filter=Q(
                    queries__submission_id=F("selected_submission_id"),
                    queries__status__in=["open", "answered"],
                ),
            ),
        )
    )
    if permissions.reviewer(request.user):
        query = query.filter(unresolved_query_count__gt=0)
    else:
        query = query.filter(
            product_scope(selected_workspace(request)),
            status="query_raised",
            organisation__memberships__user=request.user,
        )
    items = _page(
        request,
        query.select_related(
            "product__workspace",
            "application",
            "organisation",
        ).order_by("submitted_at", "pk"),
    )
    for item in items:
        item.portal_url = (
            item.get_absolute_url()
            if permissions.reviewer(request.user)
            else _integrator_item_url(item)
        )
    return render(
        request,
        "experiences/pending.html",
        _context(request, items=items, page_title="Pending queries", nav="queries"),
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def credentials(request, reference):  # noqa: C901, PLR0912
    workspace = _workspace(request, reference)
    if workspace.definition.sandbox_credentials is None:
        raise Http404
    # Reviewers can see health metadata in context, but cannot open this surface.
    permissions.require_integrator(request.user, workspace.product.organisation)
    credential = ProductCredential.objects.filter(product=workspace.product).first()
    production = production_services.state(workspace.product)
    # Integration events carry no review item; the review pages carry the rest.
    activity = workspace.product.audit_events.filter(
        item__isnull=True,
    ).select_related("actor")[:10]
    form = CallbackURLForm(
        initial={"callback_url": credential.callback_url} if credential else None,
    )

    def _secret_response(secret):
        """A revealed secret leaves no copy in a cache or a shared proxy."""
        response = render(
            request,
            "experiences/partials/secret.html"
            if request.htmx and not request.htmx.boosted
            else "experiences/credentials.html",
            _context(
                request,
                workspace,
                credential=credential,
                form=form,
                nav="credentials",
                page_title=workspace.definition.sandbox_credentials.name,
                demo_credentials=workspace.definition.sandbox_credentials.is_demo(),
                progress=provisioning_progress(workspace.product),
                production=production,
                activity=activity,
                secret=secret,
                revealed_secret=secret,
            ),
        )
        response["Cache-Control"] = "no-store, private"
        response["Vary"] = "Cookie"
        return response

    if request.method == "POST":
        if not credential:
            raise Http404
        try:
            intent = request.POST.get("intent")
            if intent == "reveal":
                return _secret_response(
                    credential_services.reveal(credential, request.user),
                )
            if intent == "rotate":
                credential_services.rotate(credential, request.user)
            elif intent == "revoke":
                credential_services.revoke(credential, request.user)
            elif intent == "check":
                credential_services.check_callback(credential, request.user)
            elif intent == "callback":
                form = CallbackURLForm(request.POST)
                if form.is_valid():
                    credential_services.save_callback_url(
                        credential,
                        request.user,
                        form.cleaned_data["callback_url"],
                    )
                else:
                    return render(
                        request,
                        "experiences/credentials.html",
                        _context(
                            request,
                            workspace,
                            credential=credential,
                            form=form,
                            nav="credentials",
                            page_title=workspace.definition.sandbox_credentials.name,
                            progress=provisioning_progress(workspace.product),
                            production=production,
                            activity=activity,
                        ),
                    )
            else:
                msg = "Choose a valid credential action."
                raise ValidationError(msg)  # noqa: TRY301
            messages.success(
                request,
                "Credentials updated."
                if intent != "check"
                else "Callback check complete.",
            )
            return redirect("experiences:credentials", reference=reference)
        except ValidationError as error:
            if request.POST.get("intent") == "reveal" and request.htmx:
                return render(
                    request,
                    "experiences/partials/secret.html",
                    {
                        "error": " ".join(error.messages),
                        "credential": credential,
                        "workspace": workspace,
                    },
                )
            _error(request, error)
    return render(
        request,
        "experiences/credentials.html",
        _context(
            request,
            workspace,
            credential=credential,
            form=form,
            nav="credentials",
            page_title=workspace.definition.sandbox_credentials.name,
            demo_credentials=workspace.definition.sandbox_credentials.is_demo(),
            progress=provisioning_progress(workspace.product),
            production=production,
            activity=activity,
        ),
    )


@login_required
def reference_environment(request, reference):
    workspace = _workspace(request, reference)
    environment = workspace.definition.reference_environment
    if environment is None:
        raise Http404
    permissions.require_integrator(request.user, workspace.product.organisation)
    milestones = [
        (
            workspace.definition.milestones[key],
            names,
            environment.milestone_options.get(key, ""),
            key in environment.in_progress,
        )
        for key, names in environment.flows.items()
    ]
    credential = ProductCredential.objects.filter(product=workspace.product).first()
    client_id = credential.client_id if credential else ""
    shells = [
        (key, shell, environment.command_segments(shell, client_id))
        for key, shell in environment.shells.items()
    ]
    return render(
        request,
        "experiences/reference_environment.html",
        _context(
            request,
            workspace,
            nav="reference",
            page_title="Reference environment",
            reference_environment=environment,
            reference_milestones=milestones,
            reference_shells=shells,
            reference_client_id=client_id,
            has_credential=credential is not None,
        ),
    )


@login_required
def agent_skills(request, reference):
    workspace = _workspace(request, reference)
    catalogue = workspace.definition.agent_skills
    if catalogue is None:
        raise Http404
    permissions.require_integrator(request.user, workspace.product.organisation)
    context = _context(
        request,
        workspace,
        nav="skills",
        page_title="Agent Skills",
        agent_skills=catalogue,
    )
    context["skill_groups"] = agent_skill_groups(workspace, context["tracks"])
    context["selected_skill"] = default_agent_skill(context["skill_groups"])
    # The command names one folder, so a locked skill is never offered to it.
    context["installable_skills"] = [
        row["definition"]
        for group in context["skill_groups"]
        for row in group["skills"]
        if not row["locked"]
    ]
    # An agent with a URL scheme also gets a one-click link per installable
    # skill, and the page shows the one for whichever skill is chosen.
    context["agent_targets"] = [
        (
            key,
            target,
            catalogue.command_segments(target),
            [
                (skill, catalogue.install_deeplink(target, skill))
                for skill in context["installable_skills"]
            ]
            if target.deeplink
            else [],
        )
        for key, target in catalogue.targets.items()
    ]
    return render(request, "experiences/agent_skills.html", context)


def _reviewer_required(request):
    if not permissions.has_area(request.user, "review"):
        msg = "This area is for reviewers."
        raise PermissionDenied(msg)


def _review_requests(user):
    """Reviews that are requests. A product registration is only a record."""
    return permissions.visible_reviews(user).exclude(kind=ReviewItem.Kind.PRODUCT)


def _track_filter(code):
    program = get_program()
    return permissions.track_items(program, program.track_map()[code])


def _requests(program):
    """Review requests that belong to no track, keyed by their filter value."""
    requests = {
        ReviewItem.Kind.ORGANISATION.value: (
            ReviewItem.Kind.ORGANISATION.label,
            Q(kind=ReviewItem.Kind.ORGANISATION),
        ),
    }
    certification = program.applications.certification
    if certification:
        requests["certification"] = (
            certification.filter_name or certification.name,
            Q(application__application_type=certification.key),
        )
    return requests


def _request_choices(program, user):
    """Requests offered in the Type filter, to reviewers who can see them."""
    if not permissions.has_access(user, "review", program=program.key):
        return []
    return [(value, label) for value, (label, _query) in _requests(program).items()]


def _item_filter(program, item):
    """The queue's Type filter: one request, every milestone, or one track's."""
    requests = _requests(program)
    if item in requests:
        return requests[item][1]
    if item == "milestones":
        return Q(application__milestone__isnull=False)
    if item in program.track_map():
        return _track_filter(item)
    return None


@login_required
def assess_dashboard(request):
    _reviewer_required(request)
    allowed_tracks = permissions.allowed_tracks(request.user)
    allowed_milestones = {
        key for track in allowed_tracks for key in get_program().track_milestones(track)
    }
    items = permissions.visible_reviews(request.user).exclude(status="draft")
    waiting = services.waiting_reviews()
    ready = items.filter(~waiting, status__in=services.PENDING_STATUSES)
    today = timezone.localdate()
    decisions = list(
        AuditEvent.objects.filter(
            item__in=items,
            action__in=["Approved", "Rejected"],
            created_at__gte=timezone.now() - timedelta(weeks=8),
        ),
    )
    submitted_at = dict(
        FormSubmission.objects.filter(
            pk__in=[event.detail.get("submission_id") for event in decisions],
        ).values_list("pk", "submitted_at"),
    )
    durations = [
        (event.created_at - submitted_at[event.detail["submission_id"]]).total_seconds()
        / 86400
        for event in decisions
        if event.detail.get("submission_id") in submitted_at
    ]
    weeks = []
    monday = today - timedelta(days=today.weekday())
    for index in range(7, -1, -1):
        start = monday - timedelta(weeks=index)
        end = start + timedelta(days=7)
        subset = [
            item
            for item in decisions
            if start <= timezone.localtime(item.created_at).date() < end
        ]
        weeks.append(
            {
                "label": start.strftime("%d %b"),
                "approved": sum(item.action == "Approved" for item in subset),
                "rejected": sum(item.action == "Rejected" for item in subset),
            },
        )
    maximum = max([week["approved"] + week["rejected"] for week in weeks] or [1]) or 1
    for week in weeks:
        week["approved_height"] = round(week["approved"] / maximum * 110)
        week["rejected_height"] = round(week["rejected"] / maximum * 110)
    context = _context(
        request,
        page_title="Reviewer dashboard",
        nav="assess-dashboard",
        my_open=ready.filter(assignee=request.user).count(),
        approved_by_milestone=[
            {
                "label": milestone.code,
                "name": milestone.name,
                "count": items.filter(
                    status="approved",
                    decided_at__date__gte=today.replace(day=1),
                    application__milestone__key=milestone.key,
                ).count(),
            }
            for milestone in get_program().milestones.values()
            if milestone.key in allowed_milestones
        ],
        ready_count=ready.count(),
        waiting_count=items.filter(waiting).count(),
        new_count=ready.filter(status="new").count(),
        review_count=ready.filter(status="in_review").count(),
        query_count=ready.filter(status="query_raised").count(),
        approved_month=AuditEvent.objects.filter(
            item__in=items,
            action="Approved",
            created_at__date__gte=today.replace(day=1),
        ).count(),
        median_days=round(median(durations), 1) if durations else None,
        weeks=weeks,
        by_type=ready.values("kind").annotate(count=Count("pk")),
        by_assignee=ready.values("assignee__name", "assignee__email").annotate(
            count=Count("pk"),
        ),
        by_track=[
            {
                "code": track.code,
                "count": ready.filter(_track_filter(track.code)).count(),
            }
            for track in allowed_tracks
        ],
        ageing=[
            (
                "0-2 days",
                ready.filter(
                    submitted_at__gte=timezone.now() - timedelta(days=3),
                ).count(),
            ),
            (
                "3-7 days",
                ready.filter(
                    submitted_at__lt=timezone.now() - timedelta(days=3),
                    submitted_at__gte=timezone.now() - timedelta(days=8),
                ).count(),
            ),
            (
                "8+ days",
                ready.filter(
                    submitted_at__lt=timezone.now() - timedelta(days=8),
                ).count(),
            ),
        ],
    )
    return render(request, "experiences/assess_dashboard.html", context)


QUEUE_ORDER = {
    "newest": ("-submitted_at", "-pk"),
    "oldest": ("submitted_at", "pk"),
}


def _queue_sort(request):
    sort = request.GET.get("sort")
    return sort if sort in QUEUE_ORDER else "newest"


def _prerequisite_label(program, prerequisite):
    """("M1", "under review"), or ("organisation verification", "new")."""
    review = prerequisite.review
    milestone = getattr(review.application, "milestone", None) if review else None
    name = program.milestones[milestone.key].code if milestone else prerequisite.name
    if prerequisite.withdrawn:
        return name, "withdrawn"
    if review is None or review.status == ReviewItem.Status.DRAFT:
        return name, "not submitted"
    return name, review.get_status_display().lower()


def waiting_rows(items):
    """What each request waits on, and how many requests wait on it."""
    items = list(items)
    for item in items:
        item.waiting_on = (
            [
                _prerequisite_label(item.program, prerequisite)
                for prerequisite in services.pending_prerequisites(item)
            ]
            if item.pending
            else []
        )
        item.waited_on_by = (
            len(services.pending_dependants(item))
            if item.status != ReviewItem.Status.APPROVED
            else 0
        )
    return items


def _queue_rows(page, user, matching):
    """The page's entries, each request marked with what it waits on and its chip."""
    page = populate_queue_page(page, user, matching)
    for item in waiting_rows(item for entry in page for item in entry.reviews):
        item.queue_state = "blocked" if item.waiting_on else item.status
    return page


@login_required
def queue(request):
    _reviewer_required(request)
    query = queue_requests(request.user)
    assignee, item, search = (
        request.GET.get(key, "") for key in ("assignee", "item", "q")
    )
    if assignee == "me":
        query = query.filter(assignee=request.user)
    elif assignee == "unassigned":
        query = query.filter(assignee=None)
    elif assignee.isdigit():
        query = query.filter(assignee_id=assignee)
    item_filter = _item_filter(get_program(), item)
    if item_filter is not None:
        query = query.filter(item_filter)
    if search:
        query = query.filter(
            Q(product__name__icontains=search)
            | Q(product__workspace__reference__icontains=search)
            | Q(organisation_product__name__icontains=search)
            | Q(organisation_product__workspace__reference__icontains=search)
            | Q(organisation__name__icontains=search)
            | Q(application__reference__icontains=search),
        )
    waiting = services.waiting_reviews()
    scopes = {
        "ready": (services.PENDING_STATUSES, ~waiting),
        "waiting": (services.PENDING_STATUSES, waiting),
        "decided": ((ReviewItem.Status.APPROVED, ReviewItem.Status.REJECTED), Q()),
    }
    stage_counts = {
        stage: grouped_requests(
            query.filter(stage_filter, status__in=stage_statuses),
        ).count()
        for stage, (stage_statuses, stage_filter) in scopes.items()
    }
    stage_counts["all"] = grouped_requests(query).count()
    scope = request.GET.get("scope", "")
    if scope != "all":
        scope = scope if scope in scopes else "ready"
        scope_statuses, scope_filter = scopes[scope]
        query = query.filter(scope_filter, status__in=scope_statuses)
    statuses = [
        (value, label)
        for value, label in ReviewItem.Status.choices
        if value != "draft" and (scope == "all" or value in scopes[scope][0])
    ]
    status = request.GET.get("status", "")
    if status in dict(statuses):
        query = query.filter(status=status)
    else:
        status = ""
    sort = _queue_sort(request)
    params = request.GET.copy()
    params.pop("page", None)
    params["scope"] = scope
    if not status:
        params.pop("status", None)
    return render(
        request,
        "experiences/queue.html",
        _context(
            request,
            page_title="Review queue",
            nav="queue",
            page=_queue_rows(
                _page(request, grouped_requests(query, sort)),
                request.user,
                query,
            ),
            stage_counts=stage_counts,
            queue_scope=scope,
            queue_sort=sort,
            statuses=statuses,
            reviewers=get_user_model().objects.filter(
                Q(is_nha_team=True) | Q(is_superuser=True),
                is_active=True,
            ),
            filters=params,
            filter_query=params.urlencode(),
            request_choices=_request_choices(get_program(), request.user),
            track_choices=permissions.allowed_tracks(request.user),
        ),
    )


def _can_provision(user, product, program_key):
    """A failed chain is an operational fault, so the console owns the re-run.

    Its usual causes — a gateway outage, a missing API-name list — are ones only
    an operator can clear, and a button the integrator cannot act on is worse
    than none. The same button starts a product that was never provisioned:
    registration only began provisioning every product once it stopped waiting
    for organisation verification.
    """
    return bool(
        product
        and permissions.has_access(user, "review", program=program_key)
        and (provisioning_can_be_retried(product) or awaiting_provisioning(product)),
    )


def _can_retry_provisioning(user, item):
    return _can_provision(
        user,
        item.product if item.product_id else None,
        item.program.key,
    )


def _provision(request, product):
    """Start or re-run the chain, and say which of the two it was."""
    started = awaiting_provisioning(product)
    start_provisioning(product, started_by=request.user)
    messages.success(
        request,
        "Provisioning started." if started else "Provisioning restarted.",
    )


@login_required
@require_http_methods(["GET", "POST"])
def review(request, pk):
    _reviewer_required(request)
    item = _item(request, pk)
    prerequisites = services.pending_prerequisites(item) if item.pending else []
    dependants = (
        [
            Prerequisite(review.title, review)
            for review in services.pending_dependants(item)
        ]
        if item.status != ReviewItem.Status.APPROVED
        else []
    )
    can_override = (
        item.pending
        and bool(services.overridden_prerequisites(item))
        and permissions.can_review(request.user, item, "approve")
    )
    actions = [
        action
        for action in permissions.available_review_actions(request.user, item)
        if action == "query" or not prerequisites or can_override
    ]
    selected_action = request.POST.get("action", request.GET.get("action"))
    if selected_action not in actions:
        selected_action = next(iter(actions), "")
    if request.method == "POST":
        try:
            if request.POST.get("intent") == "assign":
                assignee_id = request.POST.get("assignee", "")
                assignee = (
                    get_object_or_404(get_user_model(), pk=assignee_id)
                    if assignee_id.isdigit()
                    else None
                )
                services.assign_review(item, request.user, assignee)
            elif request.POST.get("intent") == "retry_provisioning":
                if not _can_retry_provisioning(request.user, item):
                    raise PermissionDenied
                _provision(request, item.product)
                return redirect(item)
            else:
                services.decide(
                    item,
                    request.user,
                    action=request.POST.get("action"),
                    note=request.POST.get("note", ""),
                    reason=request.POST.get("reason", ""),
                    field_key=request.POST.get("field_key", "form"),
                )
            messages.success(request, "Review updated.")
            return redirect(item)
        except ValidationError as error:
            _error(request, error)
    return render(
        request,
        "experiences/review.html",
        _context(
            request,
            page_title=item.reference,
            nav="queue",
            item=item,
            can_decide=permissions.can_decide(request.user, item),
            can_query=permissions.can_review(request.user, item, "write"),
            can_approve=permissions.can_review(request.user, item, "approve"),
            can_override=can_override,
            reviewers=[
                user
                for user in get_user_model().objects.filter(
                    Q(is_nha_team=True) | Q(is_superuser=True),
                    is_active=True,
                )
                if permissions.eligible_reviewer(user, item)
            ]
            if request.user.is_superuser
            else [],
            available_actions=actions,
            prerequisites=prerequisites,
            linked_prerequisites=set(
                permissions.visible_reviews(request.user)
                .filter(pk__in=[row.review.pk for row in prerequisites if row.review])
                .values_list("pk", flat=True),
            ),
            dependants=dependants,
            linked_dependants=set(
                permissions.visible_reviews(request.user)
                .filter(pk__in=[row.review.pk for row in dependants])
                .values_list("pk", flat=True),
            ),
            decision_action=selected_action,
            decision_note=request.POST.get("note", ""),
            reject_reasons=services.reject_reasons(item.definition),
            other_reason=services.OTHER_REASON,
            min_note_length=services.MIN_REVIEW_TEXT,
            decision_reason=request.POST.get("reason", ""),
            query_field=request.POST.get("field_key", request.GET.get("field", "form")),
            awaiting_reply_count=item.queries.filter(
                submission_id=item.selected_submission_id,
                status="open",
            ).count(),
            unresolved_query_count=item.queries.filter(
                submission=item.selected_submission,
            )
            .exclude(status="resolved")
            .count(),
            prior_approvals=permissions.visible_reviews(request.user)
            .filter(
                organisation=item.organisation,
                status="approved",
            )
            .exclude(pk=item.pk)[:10],
            credential=ProductCredential.objects.filter(product=item.product).first()
            if item.product_id
            and permissions.has_access(request.user, "review", program=item.program.key)
            else None,
            production=production_services.state(item.product)
            if item.product_id
            and production_services.can_view(request.user, item.program)
            else None,
            progress=provisioning_progress(item.product) if item.product_id else [],
            can_retry_provisioning=_can_retry_provisioning(request.user, item),
            provisioning_never_started=bool(
                item.product_id and awaiting_provisioning(item.product),
            ),
            certification=_certification_context(request, item.product)
            if item.product_id
            else {},
            open_tickets=permissions.visible_tickets(request.user).filter(
                organisation=item.organisation,
                status__in=["open", "awaiting_integrator"],
            )[:5],
        ),
    )


@login_required
def open_record(request, pk):
    """One link for an email: each reader lands on the page their role can open."""
    item = _item(request, pk)
    if permissions.reviewer(request.user):
        return redirect("experiences:review", pk=item.pk)
    workspace = getattr(item.product, "workspace", None) if item.product_id else None
    return redirect(workspace or "experiences:organisation")


# Uploads are PDFs; organisation logos were images before they became links.
PREVIEW_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _file_response(upload, *, preview):
    # Only these types open in the browser, and never with a guessed type, so
    # a file that could run script on this site downloads instead.
    content_type = PREVIEW_TYPES.get(Path(upload.original_name).suffix.lower())
    inline = preview and content_type is not None
    response = FileResponse(
        upload.file.open("rb"),
        as_attachment=not inline,
        filename=upload.original_name,
        content_type=content_type if inline else None,
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@never_cache
def attachment(request, pk, filename=None):
    """Download an upload, or preview it when the URL ends with its name.

    Browsers title a preview's tab from the end of its URL.
    """
    attachment = get_object_or_404(
        FormAttachment.objects.select_related("submission__form__organisation"),
        pk=pk,
        submission__in=permissions.visible_submissions(request.user),
    )
    organisation = attachment.submission.form.organisation
    if (
        not permissions.reviewer(request.user)
        and not organisation.memberships.filter(user=request.user).exists()
    ):
        raise Http404
    return _file_response(attachment, preview=filename is not None)


@login_required
def submission(request, pk, submission_id):
    item = _item(request, pk)
    snapshot = get_object_or_404(
        permissions.visible_submissions(request.user),
        pk=submission_id,
    )
    if not (
        snapshot.form_id == item.form_id
        or (
            item.application_id
            and snapshot.origin_application_id == item.application_id
        )
        or item.history.filter(
            action__in=[
                "Reused product evidence",
                "UHI participation form upgraded",
            ],
            detail__submission_id=snapshot.pk,
        ).exists()
    ):
        raise Http404
    return render(
        request,
        "experiences/submission.html",
        _context(
            request,
            item.product.workspace if item.product_id else None,
            item=item,
            snapshot=snapshot,
            page_title=(
                f"{snapshot.form.name}: submission {snapshot.submission_number}, "
                f"revision {snapshot.revision}"
            ),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def events(request):
    permissions.require_area(request.user, "events")
    workspaces = _workspaces(request.user)
    workspace = None
    if not permissions.reviewer(request.user):
        workspace = (
            workspaces.filter(reference=request.GET.get("product")).first()
            or workspaces.filter(
                reference=request.session.get("experience_product", ""),
            ).first()
            or workspaces.first()
        )
    if workspace:
        request.session["experience_product"] = workspace.reference
    if request.method == "POST":
        event = get_object_or_404(
            permissions.visible_events(request.user).upcoming(),
            pk=request.POST.get("event"),
        )
        if request.POST.get("intent") == "cancel":
            deleted, _details = EventRegistration.objects.filter(
                event=event,
                user=request.user,
            ).delete()
            if deleted:
                messages.success(request, f"Registration cancelled for {event.title}.")
        else:
            _registration, created = EventRegistration.objects.get_or_create(
                event=event,
                user=request.user,
            )
            if created:
                messages.success(request, f"You are registered for {event.title}.")
                Notification.objects.create(
                    recipient=request.user.email,
                    subject=f"{get_program().short_name}: registered for {event.title}",
                    body=(
                        f"{event.title}\n"
                        f"{timezone.localtime(event.starts_at):%d %b %Y, %H:%M %Z}\n"
                        f"{event.join_url}"
                    ),
                )
        return redirect(request.get_full_path())
    upcoming = permissions.visible_events(request.user).upcoming()
    past = permissions.visible_events(request.user).past()
    if request.GET.get("kind") in Event.Kind.values:
        upcoming = upcoming.filter(kind=request.GET["kind"])
        past = past.filter(kind=request.GET["kind"])
    registered = set(
        EventRegistration.objects.filter(user=request.user).values_list(
            "event_id",
            flat=True,
        ),
    )
    return render(
        request,
        "experiences/events.html",
        _context(
            request,
            workspace,
            page_title="Events and Activities",
            nav="events",
            events=_page(
                request,
                past if request.GET.get("period") == "past" else upcoming,
            ),
            upcoming_count=upcoming.count(),
            past_count=past.count(),
            next_event=upcoming.first(),
            registered_upcoming_count=permissions.visible_events(request.user)
            .upcoming()
            .filter(
                pk__in=registered,
            )
            .count(),
            registered=registered,
            kinds=Event.Kind.choices,
            period=request.GET.get("period", "upcoming"),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def support(request):
    permissions.require_area(request.user, "support")
    workspaces = _workspaces(request.user)
    workspace = None
    if not permissions.reviewer(request.user):
        workspace = (
            workspaces.filter(reference=request.GET.get("product")).first()
            or workspaces.filter(
                reference=request.session.get("experience_product", ""),
            ).first()
            or workspaces.first()
        )
    if workspace:
        request.session["experience_product"] = workspace.reference
    tickets = permissions.visible_tickets(request.user)
    if workspace and not permissions.reviewer(request.user):
        tickets = tickets.filter(product=workspace.product)
    form = SupportForm(
        data=request.POST if request.method == "POST" else None,
        files=request.FILES or None,
        workspace=workspace,
    )
    inbox = support_inbox(
        tickets,
        request.GET,
        form,
        reviewer=permissions.reviewer(request.user),
    )
    inbox["tickets"] = _page(request, inbox["tickets"])
    can_open_ticket = bool(
        workspace
        and not permissions.reviewer(request.user)
        and permissions.can_integrate(request.user, workspace.product.organisation),
    )
    if request.method == "POST":
        if not can_open_ticket:
            # Reviewers, and integrators without a product, have nothing to
            # raise a ticket against. Say so instead of failing the request.
            _error(
                request,
                ValidationError(
                    "Register a product before opening a ticket."
                    if not permissions.reviewer(request.user)
                    else "Only an organisation's integrators can open a ticket.",
                ),
            )
            return redirect("experiences:support")
        permissions.require_integrator(request.user, workspace.product.organisation)
        if form.is_valid():
            with transaction.atomic():
                ticket = Ticket.objects.create(
                    organisation=workspace.product.organisation,
                    product=workspace.product,
                    subject=form.cleaned_data["subject"],
                    category=form.cleaned_data["category"],
                    issue_type=form.cleaned_data["issue_type"],
                    priority=form.cleaned_data["priority"],
                    created_by=request.user,
                )
                message = post_reply(
                    ticket,
                    request.user,
                    form.cleaned_data["body"],
                    from_nha_team=False,
                )
                for upload in form.cleaned_data["attachments"]:
                    TicketAttachment.objects.create(
                        message=message,
                        file=upload,
                        original_name=upload.name,
                    )
            return redirect("experiences:ticket", reference=ticket.reference)
    return render(
        request,
        "experiences/support.html",
        _context(
            request,
            workspace,
            page_title="Support",
            nav="support",
            **inbox,
            form=form,
            creating=can_open_ticket
            and (request.GET.get("new") == "1" or request.method == "POST"),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def ticket(request, reference):
    query = permissions.visible_tickets(request.user).select_related(
        "product__workspace",
    )
    ticket = get_object_or_404(query, reference=reference)
    workspace = ticket.product.workspace
    # Docs follow the track the ticket's support category belongs to. A category
    # the program has retired falls back to reading as a track code itself.
    category = workspace.definition.support_category_map().get(ticket.category)
    track = workspace.definition.track_map().get(
        category.track if category else ticket.category,
    )
    request.session["experience_product"] = workspace.reference
    resolving = request.POST.get("intent") == "close"
    form = SupportForm(
        data=request.POST if request.method == "POST" else None,
        files=request.FILES or None,
        resolving=resolving,
    )
    for key in ("subject", "category", "issue_type", "priority"):
        del form.fields[key]
    if request.method == "POST":
        allowed = (
            permissions.can_close_ticket if resolving else permissions.can_reply_ticket
        )
        if not allowed(request.user, ticket):
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                message = post_reply(
                    ticket,
                    request.user,
                    form.cleaned_data["body"],
                    from_nha_team=permissions.reviewer(request.user),
                    resolve=resolving,
                )
                for upload in form.cleaned_data["attachments"]:
                    TicketAttachment.objects.create(
                        message=message,
                        file=upload,
                        original_name=upload.name,
                    )
            return redirect("experiences:ticket", reference=reference)
    return render(
        request,
        "experiences/ticket.html",
        _context(
            request,
            workspace,
            page_title=ticket.reference,
            nav="support",
            ticket=ticket,
            ticket_docs_url=(
                track.docs_url
                if track and track.docs_url
                else workspace.definition.docs_url
            ),
            can_reply=permissions.can_reply_ticket(request.user, ticket),
            can_close=ticket.status != Status.CLOSED
            and permissions.can_close_ticket(request.user, ticket),
            resolving=resolving,
            form=form,
        ),
    )


@login_required
@never_cache
def ticket_attachment(request, pk, filename=None):
    upload = get_object_or_404(
        TicketAttachment,
        pk=pk,
        message__ticket__in=permissions.visible_tickets(request.user),
    )
    if (
        not permissions.reviewer(request.user)
        and not upload.message.ticket.organisation.memberships.filter(
            user=request.user,
        ).exists()
    ):
        raise Http404
    return _file_response(upload, preview=filename is not None)
