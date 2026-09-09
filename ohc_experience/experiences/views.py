from datetime import timedelta
from statistics import median

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.db.models import F
from django.db.models import Q
from django.http import FileResponse
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from ohc_experience.events.models import Event
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.integrations.selectors import provisioning_can_be_retried
from ohc_experience.integrations.selectors import provisioning_progress
from ohc_experience.integrations.services import start_provisioning
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply
from ohc_experience.support.models import record_status_change

from . import credentials as credential_services
from . import permissions
from . import workflows as services
from .context_processors import navigation_context
from .context_processors import selected_workspace
from .context_processors import workspaces_for
from .forms import CredentialURLsForm
from .forms import SupportForm
from .models import AuditEvent
from .models import EventRegistration
from .models import Notification
from .models import ProductCredential
from .models import ReviewItem
from .models import ReviewQuery
from .models import TicketAttachment
from .models import TicketContext
from .presentation import overview_next_step
from .presentation import overview_progress
from .registry import get_program
from .support_presentation import support_inbox


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
    result = []
    for track in permissions.allowed_tracks(user, workspace.definition):
        tiles = []
        for key in track.keys:
            if (
                f"{track.code}:{key}" not in workspace.applied_milestones
                or key not in rows
            ):
                continue
            milestone = rows[key]
            item = milestone.application.review_item
            locked = services.milestone_locked(item)
            status = "locked" if locked else item.status
            label = (
                "Locked"
                if locked
                else (
                    "Open"
                    if item.status == "draft" and not item.selected_submission_id
                    else item.get_status_display()
                )
            )
            tiles.append(
                {
                    "milestone": milestone,
                    "item": item,
                    "definition": workspace.definition.milestones[key],
                    "status": status,
                    "label": label,
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
        result.append(
            {
                "definition": track,
                "tiles": tiles,
                "approved": sum(tile["status"] == "approved" for tile in tiles),
            },
        )
    return result


def _context(request, workspace=None, **kwargs):
    is_reviewer = permissions.reviewer(request.user)
    if workspace is None and not is_reviewer:
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
        result["query_count"] = ReviewItem.objects.filter(
            organisation=org,
            status="query_raised",
        ).count()
    return result


def _error(request, error):
    messages.error(request, " ".join(error.messages))


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
    return render(
        request,
        "experiences/products.html",
        _context(request, page_title="Products", nav="products"),
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
                    "Organisation submitted for verification. "
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
            page_title="Organisation details",
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
        .product_application.forms[0]
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
            messages.success(request, "Product submitted for registration.")
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
                messages.success(request, "Product registration submitted for review.")
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
            can_edit=item.editable or item.status == "approved",
            approved_selections=approved_selections,
        ),
    )


@login_required
def overview(request, reference):
    workspace = _workspace(request, reference)
    product = workspace.product
    visible_items = permissions.visible_reviews(request.user)
    if (
        permissions.reviewer(request.user)
        and not visible_items.filter(product=product).exists()
    ):
        raise Http404
    activity = product.audit_events.all()
    outcomes = product.outcomes.all()
    if permissions.reviewer(request.user):
        activity = activity.filter(item__in=visible_items)
        outcomes = outcomes.filter(
            source_application__in=visible_items.values("application_id"),
        )
    organisation_review = (
        visible_items.filter(
            organisation=product.organisation,
            kind="organisation_verification",
        )
        .select_related("selected_submission")
        .first()
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
        credential=ProductCredential.objects.filter(product=product).first()
        if not permissions.reviewer(request.user)
        or permissions.has_access(
            request.user,
            "review",
            program=workspace.definition.key,
        )
        else None,
        outcomes=outcomes.exclude(
            outcome_type=workspace.definition.credentials.outcome_type
            if workspace.definition.credentials
            else "",
        )[:6],
        registration=visible_items.filter(
            product=product,
            kind="product_registration",
        ).first(),
    )
    context["progress"] = overview_progress(context["tracks"])
    context["next_step"] = (
        overview_next_step(
            workspace,
            context["tracks"],
            organisation_review,
            context["registration"],
        )
        if context["can_integrate"]
        else None
    )
    return render(request, "experiences/overview.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def track(request, reference, track_code):
    workspace = _workspace(request, reference)
    if track_code not in workspace.definition.track_map():
        raise Http404
    if permissions.reviewer(request.user) and not permissions.has_access(
        request.user,
        "review",
        track_code,
        program=workspace.definition.key,
    ):
        raise Http404
    track_data = next(
        row
        for row in _tracks(workspace, request.user)
        if row["definition"].code == track_code
    )
    selected = request.GET.get("milestone", "")
    tile = next(
        (tile for tile in track_data["tiles"] if tile["definition"].key == selected),
        next(iter(track_data["tiles"]), None),
    )
    item = tile["item"] if tile else None
    form = services.build_form(item) if item else None
    if request.method == "POST":
        if item is None:
            raise Http404
        try:
            intent = request.POST.get("intent")
            if intent == "reuse":
                services.reuse_evidence(item, request.user)
            elif intent in {"draft", "submit"}:
                item, form, saved = services.save_review_form(
                    item,
                    request.user,
                    data=request.POST,
                    files=request.FILES,
                    submit=intent == "submit",
                    expected_revision=request.POST.get("revision", ""),
                )
                if not saved:
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
                            locked=services.milestone_locked(item),
                        ),
                    )
            else:
                msg = "Choose a valid form action."
                raise ValidationError(msg)  # noqa: TRY301
            messages.success(
                request,
                item.definition.submitted_message
                if intent == "submit"
                else "Evidence reused."
                if intent == "reuse"
                else "Draft saved.",
            )
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
            locked=services.milestone_locked(item) if item else "",
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
            status="query_raised",
            organisation__memberships__user=request.user,
        )
    items = list(
        query.select_related("product__workspace", "application", "organisation"),
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
    if workspace.definition.credentials is None:
        raise Http404
    # Reviewers can see health metadata in context, but cannot open this surface.
    permissions.require_integrator(request.user, workspace.product.organisation)
    credential = ProductCredential.objects.filter(product=workspace.product).first()
    form = CredentialURLsForm(
        initial={
            "callback_url": credential.callback_url,
            "bridge_url": credential.bridge_url,
        }
        if credential
        else None,
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
                page_title=workspace.definition.credentials.name,
                demo_credentials=workspace.definition.credentials.is_demo(),
                progress=provisioning_progress(workspace.product),
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
            elif intent == "urls":
                form = CredentialURLsForm(request.POST)
                if form.is_valid():
                    credential_services.save_urls(
                        credential,
                        request.user,
                        form.cleaned_data,
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
                            page_title=workspace.definition.credentials.name,
                            progress=provisioning_progress(workspace.product),
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
            page_title=workspace.definition.credentials.name,
            demo_credentials=workspace.definition.credentials.is_demo(),
            progress=provisioning_progress(workspace.product),
        ),
    )


def _reviewer_required(request):
    if not permissions.has_area(request.user, "review"):
        msg = "This area is for reviewers."
        raise PermissionDenied(msg)


def _track_filter(code):
    query = Q(pk__in=[])
    for key in get_program().track_map()[code].keys:
        query |= Q(
            application__milestone__key=key,
            product__workspace__applied_milestones__contains=[f"{code}:{key}"],
        )
    return query


@login_required
def assess_dashboard(request):
    _reviewer_required(request)
    allowed_tracks = permissions.allowed_tracks(request.user)
    allowed_milestones = {key for track in allowed_tracks for key in track.keys}
    items = permissions.visible_reviews(request.user).exclude(status="draft")
    pending = items.filter(status__in=["new", "in_review", "query_raised"])
    today = timezone.localdate()
    decisions = list(
        AuditEvent.objects.filter(
            item__in=items,
            action__in=["Approved", "Sent back"],
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
                "sent_back": sum(item.action == "Sent back" for item in subset),
            },
        )
    maximum = max([week["approved"] + week["sent_back"] for week in weeks] or [1]) or 1
    for week in weeks:
        week["approved_height"] = round(week["approved"] / maximum * 110)
        week["sent_back_height"] = round(week["sent_back"] / maximum * 110)
    context = _context(
        request,
        page_title="Reviewer dashboard",
        nav="assess-dashboard",
        my_open=pending.filter(assignee=request.user).count(),
        unassigned_count=pending.filter(assignee=None).count(),
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
        pending_count=pending.count(),
        new_count=pending.filter(status="new").count(),
        review_count=pending.filter(status="in_review").count(),
        query_count=pending.filter(status="query_raised").count(),
        approved_month=AuditEvent.objects.filter(
            item__in=items,
            action="Approved",
            created_at__date__gte=today.replace(day=1),
        ).count(),
        median_days=round(median(durations), 1) if durations else None,
        oldest=pending.exclude(status="query_raised")[:5],
        weeks=weeks,
        by_type=pending.values("kind").annotate(count=Count("pk")),
        by_assignee=pending.values("assignee__name", "assignee__email").annotate(
            count=Count("pk"),
        ),
        by_track=[
            {
                "code": track.code,
                "count": pending.filter(_track_filter(track.code)).count(),
            }
            for track in allowed_tracks
        ],
        ageing=[
            (
                "0-2 days",
                pending.filter(
                    submitted_at__gte=timezone.now() - timedelta(days=3),
                ).count(),
            ),
            (
                "3-7 days",
                pending.filter(
                    submitted_at__lt=timezone.now() - timedelta(days=3),
                    submitted_at__gte=timezone.now() - timedelta(days=8),
                ).count(),
            ),
            (
                "8+ days",
                pending.filter(
                    submitted_at__lt=timezone.now() - timedelta(days=8),
                ).count(),
            ),
        ],
    )
    return render(request, "experiences/assess_dashboard.html", context)


@login_required
def queue(request):
    _reviewer_required(request)
    query = (
        permissions.visible_reviews(request.user)
        .exclude(status="draft")
        .select_related(
            "product",
            "organisation",
            "application",
            "assignee",
        )
    )
    kind, status, assignee, track_code, search = (
        request.GET.get(key, "") for key in ("kind", "status", "assignee", "track", "q")
    )
    scope = request.GET.get("scope", "all")
    scope_statuses = {
        "open": ["new", "in_review", "query_raised"],
        "decided": ["approved", "sent_back"],
    }
    if scope in scope_statuses:
        query = query.filter(status__in=scope_statuses[scope])
    else:
        scope = "all"
    statuses = [
        (value, label)
        for value, label in ReviewItem.Status.choices
        if value != "draft" and (scope == "all" or value in scope_statuses[scope])
    ]
    if status in dict(statuses):
        query = query.filter(status=status)
    else:
        status = ""
    if assignee == "unassigned":
        query = query.filter(assignee=None)
    elif assignee.isdigit():
        query = query.filter(assignee_id=assignee)
    if track_code in get_program().track_map():
        query = query.filter(_track_filter(track_code))
    if search:
        query = query.filter(
            Q(product__name__icontains=search)
            | Q(organisation__name__icontains=search)
            | Q(application__reference__icontains=search),
        )
    queue_tabs = [
        {"value": "", "label": "All", "count": query.count()},
        {
            "value": "mine",
            "label": "Mine",
            "count": query.filter(assignee=request.user).count(),
        },
        *[
            {"value": value, "label": label, "count": query.filter(kind=value).count()}
            for value, label in ReviewItem.Kind.choices
        ],
    ]
    if kind == "mine":
        query = query.filter(assignee=request.user)
    elif kind in ReviewItem.Kind.values:
        query = query.filter(kind=kind)
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
            page=Paginator(query, 20).get_page(request.GET.get("page")),
            queue_tabs=queue_tabs,
            queue_scope=scope,
            kinds=ReviewItem.Kind.choices,
            statuses=statuses,
            reviewers=get_user_model().objects.filter(
                Q(is_ohc_team=True) | Q(is_superuser=True),
                is_active=True,
            ),
            filters=params,
            filter_query=params.urlencode(),
            track_choices=permissions.allowed_tracks(request.user),
        ),
    )


def _can_retry_provisioning(user, item):
    """A failed chain is an operational fault, so the console owns the re-run.

    Its usual causes — a gateway outage, a missing API-name list — are ones only
    an operator can clear, and a button the integrator cannot act on is worse
    than none.
    """
    return bool(
        item.product_id
        and permissions.has_access(user, "review", program=item.program.key)
        and provisioning_can_be_retried(item.product),
    )


@login_required
@require_http_methods(["GET", "POST"])
def review(request, pk):
    _reviewer_required(request)
    item = _item(request, pk)
    actions = permissions.available_review_actions(request.user, item)
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
                start_provisioning(item.product, started_by=request.user)
                messages.success(request, "Provisioning restarted.")
                return redirect(item)
            else:
                services.decide(
                    item,
                    request.user,
                    action=request.POST.get("action"),
                    note=request.POST.get("note", ""),
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
            reviewers=[
                user
                for user in get_user_model().objects.filter(
                    Q(is_ohc_team=True) | Q(is_superuser=True),
                    is_active=True,
                )
                if permissions.eligible_reviewer(user, item)
            ]
            if request.user.is_superuser
            else [],
            available_actions=actions,
            decision_action=selected_action,
            decision_note=request.POST.get("note", ""),
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
            progress=provisioning_progress(item.product) if item.product_id else [],
            can_retry_provisioning=_can_retry_provisioning(request.user, item),
            open_tickets=permissions.visible_tickets(request.user).filter(
                organisation=item.organisation,
                status__in=["open", "awaiting_vendor"],
            )[:5],
        ),
    )


@login_required
@never_cache
def attachment(request, pk):
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
    response = FileResponse(
        attachment.file.open("rb"),
        as_attachment=True,
        filename=attachment.original_name,
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def submission(request, pk, submission_id):
    item = _item(request, pk)
    snapshot = get_object_or_404(
        permissions.visible_submissions(request.user),
        form=item.form,
        pk=submission_id,
    )
    return render(
        request,
        "experiences/submission.html",
        _context(
            request,
            item.product.workspace if item.product_id else None,
            item=item,
            snapshot=snapshot,
            page_title=(
                f"{item.form.name}: submission {snapshot.submission_number}, "
                f"revision {snapshot.revision}"
            ),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def events(request):
    permissions.require_area(request.user, "events")
    workspaces = _workspaces(request.user)
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
            page_title="Events",
            nav="events",
            events=past if request.GET.get("period") == "past" else upcoming,
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
        tickets = tickets.filter(experience_context__product=workspace.product)
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
    if request.method == "POST":
        if not workspace:
            msg = "Register a product before opening a ticket."
            raise ValidationError(msg)
        permissions.require_integrator(request.user, workspace.product.organisation)
        if form.is_valid():
            with transaction.atomic():
                ticket = Ticket.objects.create(
                    organisation=workspace.product.organisation,
                    subject=form.cleaned_data["subject"],
                    category=form.cleaned_data["category"],
                    priority=form.cleaned_data["priority"],
                    created_by=request.user,
                )
                TicketContext.objects.create(
                    ticket=ticket,
                    product=workspace.product,
                    track=form.cleaned_data["track"],
                )
                message = post_reply(
                    ticket,
                    request.user,
                    form.cleaned_data["body"],
                    from_ohc_team=False,
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
            creating=request.GET.get("new") == "1" or request.method == "POST",
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def ticket(request, reference):
    query = permissions.visible_tickets(request.user)
    ticket = get_object_or_404(query, reference=reference)
    context = (
        TicketContext.objects.filter(ticket=ticket)
        .select_related("product__workspace")
        .first()
    )
    workspace = context.product.workspace if context else None
    if workspace:
        request.session["experience_product"] = workspace.reference
    form = SupportForm(
        data=request.POST if request.method == "POST" else None,
        files=request.FILES or None,
    )
    for key in ("subject", "category", "track", "priority"):
        del form.fields[key]
    if request.method == "POST":
        if request.POST.get("intent") == "resolve":
            if not permissions.can_resolve_ticket(request.user, ticket):
                raise PermissionDenied
            record_status_change(ticket, request.user, "resolved")
            return redirect("experiences:ticket", reference=reference)
        if not permissions.can_reply_ticket(request.user, ticket):
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                message = post_reply(
                    ticket,
                    request.user,
                    form.cleaned_data["body"],
                    from_ohc_team=permissions.reviewer(request.user),
                )
                for upload in form.cleaned_data["attachments"]:
                    TicketAttachment.objects.create(
                        message=message,
                        file=upload,
                        original_name=upload.name,
                    )
                services.notify_ticket_reply(
                    ticket,
                    request.user,
                    form.cleaned_data["body"],
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
            ticket_context=context,
            can_reply=permissions.can_reply_ticket(request.user, ticket),
            can_resolve=permissions.can_resolve_ticket(request.user, ticket),
            form=form,
        ),
    )


@login_required
@never_cache
def ticket_attachment(request, pk):
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
    return FileResponse(
        upload.file.open("rb"),
        as_attachment=True,
        filename=upload.original_name,
    )
