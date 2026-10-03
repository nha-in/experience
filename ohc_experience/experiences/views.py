import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

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
from django.db.models import Case
from django.db.models import CharField
from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import IntegerField
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import NullIf
from django.db.models.functions import TruncMonth
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
from django.views.decorators.http import require_safe

from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences.definitions import DocumentReadError
from ohc_experience.experiences.definitions import Prerequisite
from ohc_experience.experiences.definitions import readable_list
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.integrations.selectors import awaiting_provisioning
from ohc_experience.integrations.selectors import bridge_state
from ohc_experience.integrations.selectors import latest_run
from ohc_experience.integrations.selectors import provisioning_can_be_retried
from ohc_experience.integrations.selectors import provisioning_progress
from ohc_experience.integrations.selectors import teardown_is_incomplete
from ohc_experience.integrations.services import start_deprovisioning
from ohc_experience.integrations.services import start_provisioning
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import change_priority
from ohc_experience.support.models import post_reply

from . import credentials as credential_services
from . import legacy
from . import permissions
from . import production as production_services
from . import tables
from . import workflows as services
from .context_processors import navigation_context
from .context_processors import product_scope
from .context_processors import products_for
from .context_processors import selected_product
from .forms import CallbackURLForm
from .forms import SupportForm
from .models import AuditEvent
from .models import EventRegistration
from .models import Product
from .models import ProductCredential
from .models import ReviewItem
from .models import ReviewQuery
from .models import SubmissionStatus
from .models import TicketAttachment
from .presentation import agent_skill_groups
from .presentation import default_agent_skill
from .presentation import overview_next_step
from .presentation import overview_progress
from .presentation import product_hold_step
from .presentation import recommended_step
from .presentation import track_documents
from .presentation import track_progress
from .queue_presentation import QUEUE_SORTS
from .queue_presentation import grouped_requests
from .queue_presentation import populate_queue_page
from .queue_presentation import queue_requests
from .queue_presentation import review_order
from .queue_presentation import submitted_requests
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
        # Choosing the same document again is the browser asking for another
        # reading, not for whatever the last one concluded.
        fields = form_definition.read_document(
            field_key,
            upload,
            refresh=request.POST.get("refresh") == "1",
        )
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


def _products(user):
    return products_for(user)


def _product(request, reference):
    product = get_object_or_404(_products(request.user), reference=reference)
    request.session["experience_product"] = product.reference
    return product


def _item(request, pk):
    query = permissions.visible_reviews(request.user).select_related(
        "selected_submission__submitted_by",
        "form",
        "organisation",
        "product",
        "application",
        "assignee",
        "decided_by",
    )
    if not permissions.reviewer(request.user):
        query = query.filter(organisation__memberships__user=request.user)
    return get_object_or_404(query, pk=pk)


def _off_domain_website(item, submitter):
    """The website's domain when the submitter's address is not on it, else "".

    Only the organisation's own verification is judged by its website.
    """
    if submitter and item.kind == ReviewItem.Kind.ORGANISATION:
        return item.organisation.email_off_website(submitter.email)
    return ""


def _tracks(product, user):
    rows = {
        milestone.key: milestone
        for milestone in product.milestones.filter(
            enabled=True,
        ).select_related("application__review_item")
    }
    applied = {
        track.code: product.definition.applied_keys(
            track,
            product.applied_milestones,
        )
        for track in product.definition.tracks
    }
    result = []
    for track in permissions.allowed_tracks(user, product.definition):
        tiles = []
        for key in applied[track.code]:
            if key not in rows:
                continue
            milestone = rows[key]
            definition = product.definition.milestones[key]
            # Only tracks this product actually applied for: naming one it never
            # chose is noise. The registration note carries the catalogue-wide fact.
            shared = [
                code
                for code in product.definition.shared_with(key, track.code)
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
                        args=[product.reference, track.code],
                    )
                    + f"?milestone={key}",
                },
            )
        codes = {tile["definition"].key: tile["definition"].code for tile in tiles}
        for tile in tiles:
            definition = tile["definition"]
            options = product.definition.milestone_predecessors(
                definition.key,
                product.organisation,
            )
            needs = [codes[other] for other in options if other in codes]
            tile["needs"] = readable_list(
                needs,
                conjunction="and" if definition.requires_all else "or",
            )
        result.append(
            {
                "definition": track,
                "shared_note": product.definition.shared_note(track.code),
                "tiles": tiles,
                "approved": sum(tile["status"] == "approved" for tile in tiles),
            },
        )
    return result


def _lock_tiles(product, tracks):
    """Name what each unsubmitted milestone waits on before its form opens."""
    codes = {
        milestone.application_id: product.definition.milestones[milestone.key].code
        for milestone in product.milestones.all()
    }
    unsubmitted = {}
    for tile in (tile for track in tracks for tile in track["tiles"]):
        key = tile["definition"].key
        if key not in unsubmitted:
            unsubmitted[key] = (
                services.unsubmitted_prerequisites(tile["item"])
                if tile["item"].editable
                else []
            )
        tile["locked_by"] = readable_list(
            codes.get(review.application_id, review.title)
            for review in unsubmitted[key]
        )
        tile["resubmit"] = services.all_rejected(unsubmitted[key])
        if tile["locked_by"]:
            tile["label"] = "Locked"


def _page(request, items):
    return tables.paginate(request, items, 10)


def _context(request, product=None, **kwargs):
    is_reviewer = permissions.reviewer(request.user)
    if is_reviewer:
        # Staff never work inside a product: the switcher and the product's own
        # navigation belong to its integrators. Staff pages link to products instead.
        product = None
    elif product is None:
        product = selected_product(request)
    request.experience_navigation = navigation_context(request, product)
    result = {
        **request.experience_navigation,
        "experience_program": product.definition if product else get_program(),
        "reviewer": is_reviewer,
        "products": _products(request.user),
        "product": product,
        "selected_product": product,
        "tracks": _tracks(product, request.user) if product else [],
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
    if product:
        result["organisation"] = product.organisation
        result["can_integrate"] = permissions.can_integrate(
            request.user,
            product.organisation,
        )
    if not is_reviewer:
        org = result.get("organisation") or _organisation(request)
        result["organisation"] = org
        result["can_integrate"] = permissions.can_integrate(request.user, org)
    return result


def _error(request, error):
    messages.error(request, " ".join(error.messages))


def _certification_context(request, product):
    program = product.definition
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
    product = (
        _products(request.user)
        .filter(
            reference=request.GET.get(
                "product",
                request.session.get("experience_product", ""),
            ),
        )
        .first()
        or _products(request.user).order_by("created_at").first()
    )
    return redirect(
        product.get_absolute_url() if product else "experiences:product-create",
    )


def _product_columns(*, reviewer=False):
    """The products table's sortable headings; staff see two more.

    A product applied as several solution types sorts by its first.
    """
    columns = {
        "product": Lower("name"),
        "solution": Lower(
            tables.labelled(
                KT("solution_type__0"),
                get_program().solution_types.items(),
            ),
        ),
        "registered": "registered_at",
    }
    if reviewer:
        columns |= {
            "organisation": Lower(tables.organisation_name("organisation__")),
            "open": "open_count",
        }
    return columns


PRODUCT_EXPORT_HEADER = (
    "Reference",
    "Product",
    "Organisation",
    "Solution type",
    "Registered on",
)
REVIEWER_PRODUCT_EXPORT_HEADER = (
    *PRODUCT_EXPORT_HEADER[:-1],
    "Open requests",
    "Registered on",
)


def _product_export_rows(products, *, open_counts=False):
    for product in products:
        yield (
            product.reference,
            product.name,
            product.organisation.display_name,
            product.get_solution_type_display(),
            *((product.open_count,) if open_counts else ()),
            tables.day(product.registered_at),
        )


@login_required
def products(request):
    permissions.require_area(request.user, "review")
    if permissions.reviewer(request.user):
        return _reviewer_products(request)
    sort, order = tables.sorting(request, _product_columns(), "product")
    rows = _products(request.user).order_by(*order)
    if export := tables.export_format(request):
        return tables.export_response(
            export,
            "products",
            PRODUCT_EXPORT_HEADER,
            _product_export_rows(rows),
        )
    return render(
        request,
        "experiences/products.html",
        _context(
            request,
            page_title="Products",
            nav="products",
            products=_page(request, rows),
            table_sort=sort,
        ),
    )


def _require_reviewer_area(user):
    if not permissions.reviewer(user):
        msg = "Only authorised reviewers can access organisations."
        raise PermissionDenied(msg)
    permissions.require_area(user, "review")


def _open_requests(user):
    return permissions.review_requests(user).filter(
        status__in=services.PENDING_STATUSES,
    )


def _product_rows(user):
    """The products a reviewer can see, each with its open request count."""
    return _products(user).annotate(
        open_count=Count(
            "review_items",
            filter=Q(review_items__in=_open_requests(user)),
            distinct=True,
        ),
    )


def _solution_type_choices(user):
    """The solution types the reviewer's products applied for, in catalogue order."""
    applied = {
        key
        for keys in _products(user)
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


def _organisation_columns():
    """The organisations table's sortable headings."""
    return {
        "name": Lower(tables.organisation_name()),
        "type": Lower(
            tables.labelled("entity_type", get_program().signup_organisation_choices),
        ),
        "state": Lower(NullIf("state", Value(""))),
        "products": "visible_product_count",
        "open": "open_count",
        "status": tables.ranked(
            "verification_status",
            Organisation.VerificationStatus.values,
        ),
        "registered": "created_at",
    }


ORGANISATION_EXPORT_HEADER = (
    "Organisation",
    "Type of entity",
    "State",
    "District",
    "Verification",
    "Products",
    "Open requests",
    "Registered on",
    "Verified on",
)


def _with_labels(organisations):
    """Each organisation with its type of entity and state as the table reads."""
    entity_types = dict(get_program().signup_organisation_choices)
    for organisation in organisations:
        organisation.entity_type_label = entity_types.get(
            organisation.entity_type,
            organisation.entity_type,
        )
        organisation.state_label = _state_label(organisation.state)
        yield organisation


def _organisation_export_rows(organisations):
    for organisation in _with_labels(organisations):
        yield (
            organisation.display_name,
            organisation.entity_type_label,
            organisation.state_label,
            organisation.city,
            organisation.get_verification_status_display(),
            organisation.visible_product_count,
            organisation.open_count,
            tables.day(organisation.created_at),
            tables.day(organisation.verified_at),
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
    )
    sort, order = tables.sorting(request, _organisation_columns(), "name")
    rows = rows.order_by(*order)
    if export := tables.export_format(request):
        return tables.export_response(
            export,
            "organisations",
            ORGANISATION_EXPORT_HEADER,
            _organisation_export_rows(rows),
        )
    page = _page(request, rows)
    page.object_list = list(_with_labels(page))
    return render(
        request,
        "experiences/organizations.html",
        _context(
            request,
            page_title="Organisations",
            nav="organizations",
            organizations=page,
            table_sort=sort,
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
        .filter(pk__in=_products(request.user).values("organisation"))
        .order_by("name")
    )
    organization = request.GET.get("organization", "")
    if organization and organization_choices.filter(slug=organization).exists():
        rows = rows.filter(organisation__slug=organization)
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
            Q(name__icontains=search) | Q(reference__icontains=search),
        )
    sort, order = tables.sorting(request, _product_columns(reviewer=True), "product")
    rows = rows.order_by(*order)
    if export := tables.export_format(request):
        return tables.export_response(
            export,
            "products",
            REVIEWER_PRODUCT_EXPORT_HEADER,
            _product_export_rows(rows, open_counts=True),
        )
    return render(
        request,
        "experiences/reviewer_products.html",
        _context(
            request,
            page_title="Products",
            nav="organizations",
            products=_page(request, rows),
            table_sort=sort,
            organization_choices=organization_choices,
            selected_organization=organization,
            solution_type_choices=solution_type_choices,
            selected_solution_type=solution_type,
            search=search,
        ),
    )


# ReviewItem.title, in the database: an application's own title, else the
# product's name, else the organisation's.
REQUEST_TITLE = Lower(
    Coalesce(
        "application__title",
        "product__name",
        tables.organisation_name("organisation__"),
    ),
)
REQUEST_EXPORT_HEADER = (
    "Reference",
    "Type",
    "Request",
    "Product",
    "Status",
    "Assignee",
    "Submitted on",
    "Age (days)",
)


def _request_export_rows(items):
    for item in items:
        yield (
            item.reference,
            item.get_kind_display(),
            item.title,
            item.product.name if item.product_id else "",
            item.get_status_display(),
            item.assignee.display_name if item.assignee_id else "Unassigned",
            tables.day(item.submitted_at),
            item.age,
        )


REQUEST_COLUMNS = {
    "request": REQUEST_TITLE,
    "product": Lower("product__name"),
    "status": tables.ranked("status", ReviewItem.Status.values),
    "assignee": Lower(
        Coalesce(
            NullIf("assignee__name", Value("")),
            "assignee__email",
            output_field=CharField(),
        ),
    ),
    "submitted": "submitted_at",
}


@login_required
def organization_detail(request, slug):
    _require_reviewer_area(request.user)
    organization = get_object_or_404(
        permissions.visible_organisations(request.user),
        slug=slug,
    )
    visible_reviews = (
        permissions.review_requests(request.user)
        .filter(organisation=organization)
        .select_related(
            "assignee",
            "application",
            "product",
            "selected_submission",
        )
        .order_by("-submitted_at", "-pk")
    )
    sort, order = tables.sorting(request, REQUEST_COLUMNS, "-submitted")
    # A draft is the integrator's unsent work, not yet a request.
    requests = visible_reviews.exclude(status="draft").order_by(*order)
    product_sort, product_order = tables.sorting(
        request,
        _product_columns(reviewer=True),
        "-open",
        param="product_sort",
    )
    products = (
        _product_rows(request.user)
        .filter(organisation=organization)
        .order_by(*product_order)
    )
    if export := tables.export_format(request):
        if request.GET.get("table") == "products":
            return tables.export_response(
                export,
                f"products-{organization.slug}",
                REVIEWER_PRODUCT_EXPORT_HEADER,
                _product_export_rows(products, open_counts=True),
            )
        return tables.export_response(
            export,
            f"review-requests-{organization.slug}",
            REQUEST_EXPORT_HEADER,
            _request_export_rows(requests),
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
            product_sort=product_sort,
            review_requests=_page(request, requests),
            table_sort=sort,
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


def _posted_product_review(request, product):
    """The request that a single-request form on the product page was sent for."""
    return get_object_or_404(
        permissions.visible_reviews(request.user).filter(
            _product_review_scope(product),
        ),
        pk=_product_review_id(request.POST.get("review_id", "")),
    )


def _posted_assignee(request):
    """The reviewer an Assign form chose, or None to leave the request unassigned."""
    assignee_id = request.POST.get("assignee", "")
    return (
        get_object_or_404(get_user_model(), pk=assignee_id)
        if assignee_id.isdigit()
        else None
    )


def _review_label(item):
    """A request as its section on the product page is headed."""
    if item.kind == ReviewItem.Kind.ORGANISATION:
        return "Organisation verification"
    return item.title


def _decision_notice(item, action, *, queries=1):
    """What a reviewer's decision did, in the words of the button they pressed."""
    if action == "query":
        raised = "Query" if queries == 1 else f"{queries} queries"
        return f"{raised} raised on {_review_label(item)}."
    outcome = "approved" if action == "approve" else "rejected"
    return f"{_review_label(item)} {outcome}."


def _assign_notice(item, assignee, actor):
    if assignee is None:
        return f"{_review_label(item)} unassigned."
    name = "you" if assignee == actor else assignee.display_name
    return f"{_review_label(item)} assigned to {name}."


def _product_review_post(request, product):
    if request.POST.get("intent") == "bulk_decision":
        decided = services.decide_product(
            product,
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
        item = _posted_product_review(request, product)
        action = request.POST.get("action")
        if action == "query":
            questions = _posted_questions(request, item)
            services.raise_queries(
                item,
                request.user,
                questions,
                expected_revision=request.POST.get("revision", ""),
            )
            notice = _decision_notice(item, action, queries=len(questions))
        else:
            services.decide(
                item,
                request.user,
                action=action,
                note=request.POST.get("note", ""),
                reason=request.POST.get("reason", ""),
                expected_revision=request.POST.get("revision", ""),
            )
            notice = _decision_notice(item, action)
        messages.success(request, notice)
        anchor = f"review-{item.pk}"
    elif request.POST.get("intent") == "assign":
        item = _posted_product_review(request, product)
        assignee = _posted_assignee(request)
        services.assign_review(item, request.user, assignee)
        messages.success(request, _assign_notice(item, assignee, request.user))
        anchor = f"review-{item.pk}"
    else:
        msg = "Choose a review action."
        raise ValidationError(msg)
    return redirect(
        reverse("experiences:product-detail", args=[product.reference]) + f"#{anchor}",
    )


def _posted_questions(request, item):
    """The questions a decision form sends, one per field it asks about.

    Each is posted as `question_<field key>`, with "form" for the whole form.
    """
    keys = ["form"]
    if item.selected_submission_id:
        keys += [field["key"] for field in item.selected_submission.field_schema]
    return [
        (key, request.POST[f"question_{key}"])
        for key in keys
        if f"question_{key}" in request.POST
    ]


def _query_drafts(request, item, action):
    """The question boxes the review page opens.

    Those sent back by a failed send keep their text; without scripts, an Ask
    link reloads the page with its field's box open.
    """
    if request.method == "POST":
        return dict(_posted_questions(request, item))
    field = request.GET.get("field")
    return {field: ""} if action == "query" and field else {}


def _product_review_sections(request, items):
    assignable = {
        item.pk for item in items if permissions.can_assign(request.user, item)
    }
    reviewers = permissions.eligible_reviewers(
        [item for item in items if item.pk in assignable],
    )
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
        submitter = snapshot.submitted_by if snapshot else None
        sections.append(
            {
                "item": item,
                "snapshot": snapshot,
                "submitter": submitter,
                "off_domain_website": _off_domain_website(item, submitter),
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
                "query_drafts": dict(_posted_questions(request, item))
                if posted
                else {},
                "can_assign": item.pk in assignable,
                "reviewers": reviewers.get(item.pk, []),
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
    product = get_object_or_404(_products(request.user), reference=reference)
    program = product.definition
    if request.method == "POST":
        if request.POST.get("intent") in CONNECTION_INTENTS:
            _connection_post(request, product, program.key)
            return redirect(
                reverse("experiences:product-detail", args=[product.reference])
                + "#connection",
            )
        try:
            return _product_review_post(request, product)
        except ValidationError as error:
            _error(request, error)
    visible_items = permissions.visible_reviews(request.user)
    product_items = sorted(
        visible_items.filter(_product_review_scope(product))
        .exclude(kind=ReviewItem.Kind.PRODUCT)
        .filter(permissions.shown_to_reviewers())
        .select_related(
            "selected_submission__form",
            "selected_submission__submitted_by",
            "form",
            "organisation",
            "product",
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
            product_scope(product),
            organisation=product.organisation,
            status__in=["new", "in_review", "query_raised"],
        ).select_related(
            "application__milestone__product",
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
    tracks = _tracks(product, request.user)
    progress = overview_progress(tracks)
    submitted_ids = {item.pk for item in product_items}
    for row in tracks:
        row["tiles"] = [
            tile for tile in row["tiles"] if tile["item"].pk in submitted_ids
        ]
        for tile in row["tiles"]:
            tile["url"] = f"#review-{tile['item'].pk}"
    general_access = permissions.has_access(request.user, "review", program=program.key)
    credential = (
        ProductCredential.objects.filter(product=product).first()
        if general_access
        else None
    )
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
            reference=product.reference,
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
            credential=credential,
            can_revoke_credentials=_can_revoke_credentials(request.user, credential),
            can_reprovision=_can_reprovision(request.user, product, credential),
            can_retry_deprovisioning=_can_retry_deprovisioning(
                request.user,
                product,
                credential,
            ),
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
                noun = capfirst(item.organisation.noun)
                # Only the first submission leads on to registration; later edits
                # come from the settings nav and belong back on this page.
                if org.products.exists():
                    messages.success(request, f"{noun} submitted for verification.")
                    return redirect("experiences:organisation")
                messages.success(
                    request,
                    f"{noun} submitted for verification. "
                    "You can register your product while it is reviewed.",
                )
                return redirect("experiences:product-create")
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
    program = get_program()
    form = program.applications.product.forms[0].form_class(
        data=request.POST if request.method == "POST" else None,
        **program.product_form_kwargs(org),
    )
    if request.method == "POST":
        product, form = services.register_product(
            org,
            request.user,
            data=request.POST,
        )
        if product:
            messages.success(request, "Product registered.")
            return redirect(product)
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
    product = _product(request, reference)
    permissions.require_integrator(request.user, product.organisation)
    item = product.review_items.get(kind="product_registration")
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
                return redirect(product)
        except ValidationError as error:
            _error(request, error)
    approved_selections = [
        value
        for value in product.applied_milestones
        if product.milestones.filter(
            key=value.split(":", 1)[1],
            application__status="approved",
        ).exists()
    ]
    under_review_selections = [
        value
        for value in product.applied_milestones
        if product.milestones.filter(
            key=value.split(":", 1)[1],
            application__review_item__status__in=services.PENDING_STATUSES,
        ).exists()
    ]
    return render(
        request,
        "experiences/product_form.html",
        _context(
            request,
            product,
            item=item,
            form=form,
            page_title="Edit product",
            nav="edit",
            can_edit=services.can_edit_review(item),
            edit_blocked=services.edit_blocked_reason(item),
            gap_notices=legacy.notices(product),
            approved_selections=approved_selections,
            under_review_selections=under_review_selections,
        ),
    )


@login_required
def overview(request, reference):
    if permissions.reviewer(request.user):
        return redirect("experiences:product-detail", reference=reference)
    product = _product(request, reference)
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
        product,
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
            outcome_type=product.definition.sandbox_credentials.outcome_type
            if product.definition.sandbox_credentials
            else "",
        )[:6],
        certification=certification,
    )
    _lock_tiles(product, context["tracks"])
    registration = product.registration
    context["edit_blocked"] = (
        services.edit_blocked_reason(registration) if registration else ""
    )
    context["progress"] = overview_progress(context["tracks"])
    context["next_step"] = (
        overview_next_step(product, context["tracks"], organisation_review)
        if context["can_integrate"]
        else None
    )
    context["handoffs"] = []
    if context["can_integrate"]:
        for key, definition in product.definition.handoffs.items():
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
    product = _product(request, reference)
    permissions.require_integrator(request.user, product.organisation)
    definition = product.definition.handoffs.get(handoff_key)
    if definition is None:
        raise Http404
    option = request.POST.get("option", "")
    available = {row["key"] for row in definition.options(product, actor=request.user)}
    try:
        url = definition.create_url(product, option=option, actor=request.user)
    except ValidationError as error:
        _error(request, error)
        result = "blocked"
        response = redirect(product.get_absolute_url())
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
    product = _product(request, reference)
    permissions.require_integrator(request.user, product.organisation)
    definition = product.definition.applications.certification
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
                    product.reference,
                )
        except ValidationError as error:
            _error(request, error)
        certification = _certification_context(request, product)
    return render(
        request,
        "experiences/certification.html",
        _context(
            request,
            product,
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
    product = get_object_or_404(_products(request.user), reference=reference)
    program = product.definition
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
            product=product,
            application__milestone__key=key,
            application__milestone__enabled=True,
        )
        .first()
        if key in program.applied_keys(track, product.applied_milestones)
        else None
    )
    if item:
        return redirect(item)
    return redirect(
        reverse("experiences:product-detail", args=[product.reference])
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
            ("start_date", "Start date"),
            ("end_date", "End date"),
        )
    ]


def _page_milestones(track):
    """What a track page shows: its own milestones and the related ones beside them."""
    return [tile["definition"].key for tile in track["tiles"]]


def _track_evidence_context(request, item, form, track):
    if not item or not permissions.can_integrate(request.user, item.organisation):
        return {}
    choices = []
    additional_forms = getattr(form, "additional_forms", {})
    selected = set(request.POST.getlist("additional_reviews"))
    offered = services.submission_choices(item, _page_milestones(track))
    for target in sorted(offered, key=review_order):
        milestone = target.program.milestones[target.application.milestone.key]
        token = f"{target.pk}:{target.selected_submission_id or ''}"
        choice = {
            "item": target,
            "code": milestone.code,
            "name": milestone.name,
            "token": token,
            "blocked": target.submission_blocked,
            "selected": False,
            "dates": [],
            "requires": [],
        }
        if not target.submission_blocked:
            choice["selected"] = token in selected
            choice["dates"] = _milestone_testing_dates(
                request,
                prefix=f"milestone_{target.pk}_",
                errors=additional_forms[target.pk].errors
                if target.pk in additional_forms
                else None,
            )
            choice["requires"] = [
                str(prerequisite.pk)
                for prerequisite in services.unsubmitted_prerequisites(target)
                if prerequisite.pk != item.pk
            ]
        choices.append(choice)
    return {
        "submission_choices": choices,
        # Blocked choices still render, but there is no group to speak of yet.
        "submission_group_available": any(not choice["blocked"] for choice in choices),
    }


def _save_track_evidence(request, item, track):
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
                page_keys=_page_milestones(track),
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
    product = _product(request, reference)
    if track_code not in product.definition.track_map():
        raise Http404
    rows = _tracks(product, request.user)
    track_data = next(row for row in rows if row["definition"].code == track_code)
    _lock_tiles(product, [track_data])
    track_data["progress"] = track_progress(track_data)
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
            item, form, saved, notice = _save_track_evidence(
                request,
                item,
                track_data,
            )
            if saved:
                messages.success(request, notice)
                return redirect(request.get_full_path())
        except ValidationError as error:
            _error(request, error)
    next_step = product_hold_step(product)
    if not next_step and item and item.status == ReviewItem.Status.APPROVED:
        # Once this milestone is done, point at the work left on this track. The
        # overview is where the rest of the product's work is recommended.
        next_step = recommended_step([track_data])
    return render(
        request,
        "experiences/track.html",
        _context(
            request,
            product,
            page_title=track_code,
            nav=track_code,
            track=track_data,
            tile=tile,
            next_step=next_step,
            documents=track_documents(track_data),
            documents_view=request.GET.get("view") == "documents",
            callback_missing=item and services.callback_missing(item),
            item=item,
            form=form,
            can_edit=services.can_edit_review(item) if item else False,
            locked_by=locked_by,
            **_track_evidence_context(request, item, form, track_data),
        ),
    )


def _integrator_item_url(item):
    if item.kind == "organisation_verification":
        return reverse("experiences:organisation")
    if item.kind == "product_registration":
        return reverse(
            "experiences:product-edit",
            args=[item.product.reference],
        )
    certification = item.program.applications.certification
    if certification and item.application.application_type == certification.key:
        return reverse(
            "experiences:product-certification",
            args=[item.product.reference],
        )
    key = item.application.milestone.key
    # A product that dropped a milestone keeps the requests that depend on it,
    # so fall back to the track the catalog files it under.
    track_code = next(
        (
            value.split(":")[0]
            for value in item.product.applied_milestones
            if value.endswith(f":{key}")
        ),
        next((track.code for track in item.program.tracks_with(key)), ""),
    )
    return (
        reverse(
            "experiences:track",
            args=[item.product.reference, track_code],
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
            messages.success(request, "Query resolved.")
        else:
            services.reply_query(query, request.user, request.POST.get("body", ""))
            messages.success(request, "Reply sent.")
    except ValidationError as error:
        _error(request, error)
    return_reference = request.POST.get("return_to_product")
    if return_reference and permissions.reviewer(request.user):
        if return_reference == "1" and item.product_id:
            return_reference = item.product.reference
        product = _products(request.user).filter(reference=return_reference).first()
        if (
            product
            and permissions.visible_reviews(request.user)
            .filter(
                _product_review_scope(product),
                pk=item.pk,
            )
            .exists()
        ):
            return redirect(
                reverse(
                    "experiences:product-detail",
                    args=[product.reference],
                )
                + f"#review-{item.pk}",
            )
    return redirect(
        item.get_absolute_url()
        if permissions.reviewer(request.user)
        else _integrator_item_url(item),
    )


PENDING_COLUMNS = {
    "request": REQUEST_TITLE,
    "type": Lower(tables.labelled("kind", ReviewItem.Kind.choices)),
    "organisation": Lower(tables.organisation_name("organisation__")),
    "submitted": "submitted_at",
    # As _query_state reads it: a reply awaited first, then replies to read.
    "status": Case(
        When(awaiting_reply_count__gt=0, then=Value(0)),
        When(unresolved_query_count__gt=0, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    ),
}
PENDING_EXPORT_HEADER = (
    "Reference",
    "Type",
    "Request",
    "Product",
    "Organisation",
    "Status",
    "Open queries",
    "Submitted on",
)


def _query_state(item, *, reviewer):
    """Where a request's queries stand, as its badge in the list reads."""
    if not reviewer:
        if item.awaiting_reply_count:
            return "Action required from applicant"
        return "With reviewer"
    if item.awaiting_reply_count:
        return "Awaiting reply"
    if item.unresolved_query_count:
        return "Replies received"
    return "Ready for decision"


def _pending_export_rows(items, *, reviewer):
    for item in items:
        yield (
            item.reference,
            item.get_kind_display(),
            item.title,
            item.product.name if item.product_id else "",
            item.organisation.display_name,
            _query_state(item, reviewer=reviewer),
            item.unresolved_query_count,
            tables.day(item.submitted_at),
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
    reviewer = permissions.reviewer(request.user)
    if reviewer:
        query = query.filter(unresolved_query_count__gt=0)
    else:
        query = query.filter(
            product_scope(selected_product(request)),
            status="query_raised",
            organisation__memberships__user=request.user,
        )
    sort, order = tables.sorting(request, PENDING_COLUMNS, "submitted")
    query = query.select_related(
        "product",
        "application",
        "organisation",
        "assignee",
    ).order_by(*order)
    if export := tables.export_format(request):
        return tables.export_response(
            export,
            "pending-queries",
            PENDING_EXPORT_HEADER,
            _pending_export_rows(query, reviewer=reviewer),
        )
    items = _page(request, query)
    for item in items:
        item.portal_url = (
            item.get_absolute_url() if reviewer else _integrator_item_url(item)
        )
        item.query_state = _query_state(item, reviewer=reviewer)
    return render(
        request,
        "experiences/pending.html",
        _context(
            request,
            items=items,
            page_title="Pending queries",
            nav="queries",
            table_sort=sort,
        ),
    )


def _credential_notice(intent, credential):
    """Registration runs on a worker, so a save cannot report its outcome."""
    if intent == "callback" and not credential.callback_url:
        return "Callback URL cleared."
    if intent == "callback":
        return (
            "Callback URL saved. Registering it with the gateway — reload in a few "
            "minutes to see whether it went through."
        )
    if intent == "register":
        return (
            "Registering your callback URL with the gateway again — reload in a few "
            "minutes to see whether it went through."
        )
    return "Credentials updated."


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def credentials(request, reference):  # noqa: C901
    product = _product(request, reference)
    if product.definition.sandbox_credentials is None:
        raise Http404
    # Reviewers can see health metadata in context, but cannot open this surface.
    permissions.require_integrator(request.user, product.organisation)
    credential = ProductCredential.objects.filter(product=product).first()
    production = production_services.state(product)
    # Integration events carry no review item; the review pages carry the rest.
    activity = product.audit_events.filter(
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
                product,
                credential=credential,
                form=form,
                nav="credentials",
                page_title=product.definition.sandbox_credentials.name,
                demo_credentials=product.definition.sandbox_credentials.is_demo(),
                progress=provisioning_progress(product),
                bridge=bridge_state(product),
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
            elif intent == "register":
                credential_services.retry_bridge(credential, request.user)
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
                            product,
                            credential=credential,
                            form=form,
                            nav="credentials",
                            page_title=product.definition.sandbox_credentials.name,
                            progress=provisioning_progress(product),
                            bridge=bridge_state(product),
                            production=production,
                            activity=activity,
                        ),
                    )
            else:
                msg = "Choose a valid credential action."
                raise ValidationError(msg)  # noqa: TRY301
            # `save_callback_url` wrote through its own locked copy.
            credential.refresh_from_db()
            messages.success(request, _credential_notice(intent, credential))
            return redirect("experiences:credentials", reference=reference)
        except ValidationError as error:
            if request.POST.get("intent") == "reveal" and request.htmx:
                return render(
                    request,
                    "experiences/partials/secret.html",
                    {
                        "error": " ".join(error.messages),
                        "credential": credential,
                        "product": product,
                    },
                )
            _error(request, error)
    return render(
        request,
        "experiences/credentials.html",
        _context(
            request,
            product,
            credential=credential,
            form=form,
            nav="credentials",
            page_title=product.definition.sandbox_credentials.name,
            demo_credentials=product.definition.sandbox_credentials.is_demo(),
            progress=provisioning_progress(product),
            bridge=bridge_state(product),
            production=production,
            activity=activity,
        ),
    )


@login_required
def reference_environment(request, reference):
    product = _product(request, reference)
    environment = product.definition.reference_environment
    if environment is None:
        raise Http404
    permissions.require_integrator(request.user, product.organisation)
    milestones = [
        (
            product.definition.milestones[key],
            names,
            environment.milestone_options.get(key, ""),
            key in environment.in_progress,
        )
        for key, names in environment.flows.items()
    ]
    credential = ProductCredential.objects.filter(product=product).first()
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
            product,
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
    product = _product(request, reference)
    catalogue = product.definition.agent_skills
    if catalogue is None:
        raise Http404
    permissions.require_integrator(request.user, product.organisation)
    context = _context(
        request,
        product,
        nav="skills",
        page_title="Agent Skills",
        agent_skills=catalogue,
    )
    context["skill_groups"] = agent_skill_groups(product, context["tracks"])
    context["selected_skill"] = default_agent_skill(
        context["skill_groups"],
        context["tracks"],
    )
    context["installable_skills"] = [
        row["definition"]
        for group in context["skill_groups"]
        for row in group["skills"]
    ]
    # Each app an agent can be opened in gets a one-click link per installable
    # skill, and the page shows the ones for whichever skill is chosen.
    context["agent_targets"] = [
        (
            key,
            target,
            catalogue.command_segments(target),
            [
                (
                    link,
                    [
                        (skill, catalogue.install_deeplink(link, target, skill))
                        for skill in context["installable_skills"]
                    ],
                )
                for link in target.deeplinks
            ],
        )
        for key, target in catalogue.targets.items()
    ]
    return render(request, "experiences/agent_skills.html", context)


def _reviewer_required(request):
    if not permissions.has_area(request.user, "review"):
        msg = "This area is for reviewers."
        raise PermissionDenied(msg)


def _track_filter(code):
    program = get_program()
    return permissions.track_items(program.track_map()[code])


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


def _queue_url(**params):
    return f"{reverse('experiences:queue')}?{urlencode(params)}"


#: Audit actions that approve a request; "Recorded" needs no reviewer.
APPROVALS = ("Approved", "Approved (prerequisites overridden)", "Recorded")


def _last_twelve_months(today):
    """The first day of this month and of the eleven before it, oldest first."""
    months = [today.replace(day=1)]
    for _ in range(11):
        months.insert(0, (months[0] - timedelta(days=1)).replace(day=1))
    return months


def _decisions_by_month(requests, months):
    counts = Counter()
    decisions = (
        AuditEvent.objects.filter(
            item__in=requests,
            action__in=[*APPROVALS, "Rejected"],
            created_at__date__gte=months[0],
        )
        .annotate(month=TruncMonth("created_at"))
        .values("month", "action")
        .annotate(count=Count("pk"))
    )
    for row in decisions:
        outcome = "rejected" if row["action"] == "Rejected" else "approved"
        counts[row["month"].date(), outcome] += row["count"]
    return [
        {
            "start": month,
            "approved": counts[month, "approved"],
            "rejected": counts[month, "rejected"],
        }
        for month in months
    ]


def _bar_height(count, busiest):
    return max(2, round(count / busiest * 80)) if count else 0


def _status_card(user, item, title, caption, months):
    """The queue's rows for one Type filter, and its decisions in each month.

    An organisation holds a single verification, which the queue repeats on
    every product it owns; counted that way an organisation would be counted
    once per product. A milestone request belongs to one product, so a track
    counts the queue's own rows.
    """
    item_filter = _item_filter(get_program(), item)
    verifications = item == ReviewItem.Kind.ORGANISATION.value
    requests = (
        submitted_requests(user) if verifications else queue_requests(user)
    ).filter(item_filter)

    def rows(query):
        return query.count() if verifications else grouped_requests(query).count()

    decisions = _decisions_by_month(
        permissions.visible_reviews(user).filter(item_filter),
        months,
    )
    busiest = max(month["approved"] + month["rejected"] for month in decisions)
    for month in decisions:
        month["approved_height"] = _bar_height(month["approved"], busiest)
        month["rejected_height"] = _bar_height(month["rejected"], busiest)
    return {
        "title": title,
        "caption": caption,
        "total": rows(requests),
        "url": _queue_url(scope="all", item=item),
        "tiles": [
            {
                "label": "Pending",
                "variant": "info",
                "count": rows(requests.filter(status__in=services.PENDING_STATUSES)),
                "url": _queue_url(scope="ready", item=item),
            },
            {
                "label": "Rejected",
                "variant": "warning",
                "count": rows(requests.filter(status=ReviewItem.Status.REJECTED)),
                "url": _queue_url(scope="all", status="rejected", item=item),
            },
            {
                "label": "Approved",
                "variant": "success",
                "count": rows(requests.filter(status=ReviewItem.Status.APPROVED)),
                "url": _queue_url(scope="all", status="approved", item=item),
            },
        ],
        "months": decisions,
        "this_month": decisions[-1],
        "busiest": busiest,
    }


@login_required
def assess_dashboard(request):
    _reviewer_required(request)
    program = get_program()
    months = _last_twelve_months(timezone.localdate())
    ready = permissions.visible_reviews(request.user).filter(
        ~services.waiting_reviews(),
        status__in=services.PENDING_STATUSES,
    )
    context = _context(
        request,
        # Superusers administer the portal; Staff & permissions is theirs alone.
        page_title="Administrator dashboard"
        if request.user.is_superuser
        else "Reviewer dashboard",
        nav="assess-dashboard",
        my_open=ready.filter(assignee=request.user).count(),
        sandbox_card=_status_card(
            request.user,
            ReviewItem.Kind.ORGANISATION.value,
            "Sandbox access",
            "Organisation verification",
            months,
        )
        if permissions.has_access(request.user, "review", program=program.key)
        else None,
        track_cards=[
            _status_card(request.user, track.code, track.code, track.name, months)
            for track in permissions.allowed_tracks(request.user)
        ],
    )
    return render(request, "experiences/assess_dashboard.html", context)


# The queue's sort once took these names, which saved links still carry.
QUEUE_SORT_ALIASES = {"newest": "-submitted", "oldest": "submitted"}


def _queue_sort(request):
    sort = request.GET.get("sort", "")
    sort = QUEUE_SORT_ALIASES.get(sort, sort)
    return sort if sort in QUEUE_SORTS else "-submitted"


REVIEW_REFERENCE = re.compile(r"REV-?(\d{1,9})", re.IGNORECASE)


def _queue_search(search):
    """Requests whose product, organisation or reference matches the search.

    A product matches through the entry it belongs to, `queue_product_id`. A
    filter on `organisation_product` would join the organisation's products a
    second time, and so put its verification on every one of its products as
    soon as any of them matched.
    """
    products = Product.objects.filter(
        Q(name__icontains=search) | Q(reference__icontains=search),
    )
    matches = (
        Q(queue_product_id__in=products.values("pk"))
        | Q(organisation__name__icontains=search)
        | Q(organisation__legal_name__icontains=search)
        | Q(application__reference__icontains=search)
    )
    reference = REVIEW_REFERENCE.fullmatch(search)
    if reference:
        matches |= Q(pk=int(reference[1]))
    return matches


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


QUEUE_EXPORT_HEADER = (
    "Reference",
    "Product / request",
    "Organisation",
    "Requests",
    "Approved",
    "Submitted on",
    "Age (days)",
    "Assignees",
)


def _queue_request_label(item):
    """A request as its chip reads: "M2 Under review, waiting on M1"."""
    if item.queue_milestone:
        name = item.queue_milestone.code
    elif item.product:
        name = item.application.reference
    else:
        name = "Organisation verification"
    label = f"{name} {item.queue_label}"
    if item.waiting_on:
        label += ", waiting on " + ", ".join(
            prerequisite for prerequisite, _status in item.waiting_on
        )
    return label


def _queue_export_rows(groups, user, matching):
    """Every entry the filters match, built as one page of the queue."""
    entries = _queue_rows(
        Paginator(groups, max(groups.count(), 1)).page(1),
        user,
        matching,
    )
    for entry in entries:
        yield (
            entry.reference,
            entry.title,
            entry.organisation.display_name,
            "; ".join(_queue_request_label(item) for item in entry.matching_reviews),
            f"{len(entry.approved)} of {len(entry.reviews)}",
            tables.day(entry.submitted_at),
            entry.age,
            ", ".join(
                assignee.display_name if assignee else "Unassigned"
                for assignee in entry.assignees
            ),
        )


@login_required
def queue(request):
    _reviewer_required(request)
    query = queue_requests(request.user)
    assignee, item, search = (
        request.GET.get(key, "") for key in ("assignee", "item", "q")
    )
    search = search.strip()
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
        query = query.filter(_queue_search(search))
    # Pending holds every open request, one waiting on a prerequisite included,
    # so a product's submissions show together; its row says what each waits on.
    # Done holds only products with nothing open, so the two tabs add up to All.
    open_products = query.filter(
        status__in=services.PENDING_STATUSES,
        queue_product_id__isnull=False,
    ).values("queue_product_id")
    scopes = {
        "ready": (services.PENDING_STATUSES, Q()),
        "decided": (
            (ReviewItem.Status.APPROVED, ReviewItem.Status.REJECTED),
            Q(queue_product_id=None) | ~Q(queue_product_id__in=open_products),
        ),
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
    if export := tables.export_format(request):
        return tables.export_response(
            export,
            "review-queue",
            QUEUE_EXPORT_HEADER,
            _queue_export_rows(grouped_requests(query, sort), request.user, query),
        )
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
            table_sort=sort,
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
    than none. The same button starts a verified product that was never
    provisioned.
    """
    return bool(
        product
        and product.organisation.is_verified
        and permissions.has_access(user, "review", program=program_key)
        and (provisioning_can_be_retried(product) or awaiting_provisioning(product)),
    )


CONNECTION_INTENTS = {
    "retry_provisioning",
    "revoke_credentials",
    "reprovision_credentials",
    "retry_deprovisioning",
}


def _connection_post(request, product, program_key):
    """The Integration connection card's actions on the staff product page."""
    intent = request.POST.get("intent")
    credential = ProductCredential.objects.filter(product=product).first()
    if intent == "retry_provisioning":
        if not _can_provision(request.user, product, program_key):
            raise PermissionDenied
        _provision(request, product)
    elif intent == "revoke_credentials":
        if not _can_revoke_credentials(request.user, credential):
            raise PermissionDenied
        credential_services.revoke(credential, request.user)
        messages.success(request, "Credentials revoked. Deprovisioning started.")
    elif intent == "reprovision_credentials":
        if not _can_reprovision(request.user, product, credential):
            raise PermissionDenied
        start_provisioning(product, started_by=request.user)
        messages.success(request, "Reprovisioning started.")
    elif intent == "retry_deprovisioning":
        if not _can_retry_deprovisioning(request.user, product, credential):
            raise PermissionDenied
        start_deprovisioning(product)
        messages.success(request, "Deprovisioning restarted.")


def _can_revoke_credentials(user, credential):
    """Revoking switches the integrator off in every external system, and only
    a super admin may do that."""
    return bool(credential and credential.status == "active" and user.is_superuser)


def _can_retry_deprovisioning(user, product, credential):
    """Revoked, but some system is still switched on. The steps skip what is
    already off, so a re-run finishes only what is left."""
    return bool(
        credential
        and credential.status == "revoked"
        and user.is_superuser
        and teardown_is_incomplete(product),
    )


def _can_reprovision(user, product, credential):
    """Bring revoked credentials back, once teardown has finished.

    A failed or never-started run is left to the provisioning button, which
    starts the same chain.
    """
    run = latest_run(product)
    return bool(
        credential
        and credential.status == "revoked"
        and user.is_superuser
        and run is not None
        and run.status == run.Status.READY
        and not teardown_is_incomplete(product),
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
    can_assign = permissions.can_assign(request.user, item)
    selected_action = request.POST.get("action", request.GET.get("action"))
    if selected_action not in actions:
        selected_action = next(iter(actions), "")
    if request.method == "POST":
        try:
            if request.POST.get("intent") == "assign":
                assignee = _posted_assignee(request)
                services.assign_review(item, request.user, assignee)
                notice = _assign_notice(item, assignee, request.user)
            elif request.POST.get("intent") == "retry_provisioning":
                if not _can_retry_provisioning(request.user, item):
                    raise PermissionDenied
                _provision(request, item.product)
                return redirect(item)
            elif request.POST.get("action") == "query":
                questions = _posted_questions(request, item)
                services.raise_queries(item, request.user, questions)
                notice = _decision_notice(item, "query", queries=len(questions))
            else:
                action = request.POST.get("action")
                services.decide(
                    item,
                    request.user,
                    action=action,
                    note=request.POST.get("note", ""),
                    reason=request.POST.get("reason", ""),
                )
                notice = _decision_notice(item, action)
            messages.success(request, notice)
            return redirect(item)
        except ValidationError as error:
            _error(request, error)
    submitter = (
        item.selected_submission.submitted_by if item.selected_submission else None
    )
    return render(
        request,
        "experiences/review.html",
        _context(
            request,
            page_title=item.reference,
            nav="queue",
            item=item,
            submitter=submitter,
            off_domain_website=_off_domain_website(item, submitter),
            can_decide=permissions.can_decide(request.user, item),
            can_query=permissions.can_review(request.user, item, "write"),
            can_raise_query="query" in actions,
            can_approve=permissions.can_review(request.user, item, "approve"),
            can_override=can_override,
            can_assign=can_assign,
            reviewers=permissions.eligible_reviewers([item])[item.pk]
            if can_assign
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
            query_drafts=_query_drafts(request, item, selected_action),
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
            if item.product_id and not getattr(item.application, "milestone", None)
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
    return redirect(item.product if item.product_id else "experiences:organisation")


# PDFs, and images: a ticket takes screenshots, and organisation logos were
# images before they became links. Office files and text download.
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
            item.product if item.product_id else None,
            item=item,
            snapshot=snapshot,
            page_title=(
                f"{snapshot.form.name}: submission {snapshot.submission_number}, "
                f"revision {snapshot.revision}"
            ),
        ),
    )


def _event_columns(user):
    """The events table's sortable headings; Registration is the viewer's own."""
    return {
        "event": Lower("title"),
        "type": Lower(tables.labelled("kind", Event.Kind.choices)),
        "date": "starts_at",
        "location": Lower(Coalesce(NullIf("location", Value("")), Value("Online"))),
        "registration": Exists(
            EventRegistration.objects.filter(event=OuterRef("pk"), user=user),
        ),
    }


EVENT_EXPORT_HEADER = ("Title", "Type", "Starts", "Ends", "Location", "Status")


def _event_export_rows(events, *, registered, manage):
    """Each event, with its registrations for its team, or "Yes" for a guest."""
    if manage:
        events = events.annotate(registration_count=Count("registrations"))
    for event in events:
        yield (
            event.title,
            event.get_kind_display(),
            f"{timezone.localtime(event.starts_at):%d/%m/%Y %H:%M}",
            f"{timezone.localtime(event.ends_at):%d/%m/%Y %H:%M}"
            if event.ends_at
            else "",
            event.location or "Online",
            "Published" if event.is_published else "Draft",
            event.registration_count
            if manage
            else ("Yes" if event.pk in registered else "No"),
        )


@login_required
@require_safe
def events(request):
    permissions.require_area(request.user, "events")
    products = _products(request.user)
    product = None
    if not permissions.reviewer(request.user):
        product = (
            products.filter(reference=request.GET.get("product")).first()
            or products.filter(
                reference=request.session.get("experience_product", ""),
            ).first()
            or products.first()
        )
    if product:
        request.session["experience_product"] = product.reference
    upcoming = permissions.visible_events(request.user).upcoming()
    past = permissions.visible_events(request.user).past()
    # Drafts reach only the team that writes them: an integrator sees published
    # events alone, so visible_events leaves them nothing to draft.
    drafts = permissions.visible_events(request.user).drafts()
    if request.GET.get("kind") in Event.Kind.values:
        upcoming = upcoming.filter(kind=request.GET["kind"])
        past = past.filter(kind=request.GET["kind"])
        drafts = drafts.filter(kind=request.GET["kind"])
    listed = {"upcoming": upcoming, "past": past, "drafts": drafts}
    period = request.GET.get("period", "upcoming")
    if period not in listed:
        period = "upcoming"
    registered = set(
        EventRegistration.objects.filter(user=request.user).values_list(
            "event_id",
            flat=True,
        ),
    )
    # Upcoming and draft sessions read soonest first; past ones, latest first.
    sort, order = tables.sorting(
        request,
        _event_columns(request.user),
        "-date" if period == "past" else "date",
    )
    shown = listed[period].order_by(*order)
    if export := tables.export_format(request):
        manage = permissions.has_area(request.user, "events")
        return tables.export_response(
            export,
            f"events-{period}",
            (*EVENT_EXPORT_HEADER, "Registrations" if manage else "Registered"),
            _event_export_rows(shown, registered=registered, manage=manage),
        )
    return render(
        request,
        "experiences/events.html",
        _context(
            request,
            product,
            page_title="Events and Activities",
            nav="events",
            events=_page(request, shown),
            upcoming_count=upcoming.count(),
            past_count=past.count(),
            draft_count=drafts.count(),
            next_event=upcoming.first(),
            registered_upcoming_count=permissions.visible_events(request.user)
            .upcoming()
            .filter(
                pk__in=registered,
            )
            .count(),
            registered=registered,
            kinds=Event.Kind.choices,
            period=period,
            table_sort=sort,
        ),
    )


TICKET_COLUMNS = {
    "subject": Lower("subject"),
    "category": "category",
    "subcategory": Lower("issue_type"),
    "priority": tables.ranked("priority", ("high", "medium", "low")),
    "status": tables.ranked("status", ("open", "awaiting_integrator", "closed")),
    "updated": "updated_at",
}


def _ticket_export_header(*, reviewer):
    return (
        "Ticket ID",
        "Subject",
        *(("Product",) if reviewer else ()),
        "Category",
        "Sub-category",
        "Priority",
        "Status",
        "Opened on",
        "Updated on",
    )


def _ticket_export_rows(tickets, *, reviewer):
    for ticket in tickets:
        yield (
            ticket.reference,
            ticket.subject,
            *((ticket.product.name if ticket.product else "",) if reviewer else ()),
            ticket.category_label,
            ticket.issue_type,
            ticket.get_priority_display(),
            ticket.queue_status_label if reviewer else ticket.get_status_display(),
            tables.day(ticket.created_at),
            tables.day(ticket.updated_at),
        )


@login_required
@require_http_methods(["GET", "POST"])
def support(request):
    permissions.require_area(request.user, "support")
    products = _products(request.user)
    product = None
    if not permissions.reviewer(request.user):
        product = (
            products.filter(reference=request.GET.get("product")).first()
            or products.filter(
                reference=request.session.get("experience_product", ""),
            ).first()
            or products.first()
        )
    if product:
        request.session["experience_product"] = product.reference
    tickets = permissions.visible_tickets(request.user)
    if product and not permissions.reviewer(request.user):
        tickets = tickets.filter(product=product)
    form = SupportForm(
        data=request.POST if request.method == "POST" else None,
        files=request.FILES or None,
        product=product,
    )
    inbox = support_inbox(
        tickets,
        request.GET,
        form,
        reviewer=permissions.reviewer(request.user),
    )
    reviewer = permissions.reviewer(request.user)
    inbox["table_sort"], order = tables.sorting(request, TICKET_COLUMNS, "-updated")
    tickets = inbox["tickets"].order_by(*order)
    if request.method == "GET" and (export := tables.export_format(request)):
        return tables.export_response(
            export,
            "support-tickets",
            _ticket_export_header(reviewer=reviewer),
            _ticket_export_rows(tickets, reviewer=reviewer),
        )
    inbox["tickets"] = _page(request, tickets)
    can_open_ticket = bool(
        product
        and not permissions.reviewer(request.user)
        and permissions.can_integrate(request.user, product.organisation),
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
        permissions.require_integrator(request.user, product.organisation)
        if form.is_valid():
            with transaction.atomic():
                ticket = Ticket.objects.create(
                    organisation=product.organisation,
                    product=product,
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
            messages.success(request, f"Ticket {ticket.reference} created.")
            return redirect("experiences:ticket", reference=ticket.reference)
    return render(
        request,
        "experiences/support.html",
        _context(
            request,
            product,
            page_title="Support",
            nav="support",
            **inbox,
            form=form,
            creating=can_open_ticket
            and (request.GET.get("new") == "1" or request.method == "POST"),
        ),
    )


def _change_ticket_priority(request, ticket):
    if not permissions.can_change_ticket_priority(request.user, ticket):
        raise PermissionDenied
    try:
        changed = change_priority(
            ticket,
            request.user,
            request.POST.get("priority", ""),
        )
    except ValidationError as error:
        _error(request, error)
    else:
        if changed:
            messages.success(
                request,
                f"Priority changed to {ticket.get_priority_display()}.",
            )
    return redirect("experiences:ticket", reference=ticket.reference)


@login_required
@require_http_methods(["GET", "POST"])
def ticket(request, reference):
    query = permissions.visible_tickets(request.user).select_related(
        "product",
    )
    ticket = get_object_or_404(query, reference=reference)
    product = ticket.product
    # Docs follow the track the ticket's support category belongs to. A category
    # the program has retired falls back to reading as a track code itself.
    category = product.definition.support_category_map().get(ticket.category)
    track = product.definition.track_map().get(
        category.track if category else ticket.category,
    )
    request.session["experience_product"] = product.reference
    if request.POST.get("intent") == "priority":
        return _change_ticket_priority(request, ticket)
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
            messages.success(
                request,
                "Ticket marked as resolved." if resolving else "Reply sent.",
            )
            return redirect("experiences:ticket", reference=reference)
    return render(
        request,
        "experiences/ticket.html",
        _context(
            request,
            product,
            page_title=ticket.reference,
            nav="support",
            ticket=ticket,
            ticket_docs_url=(
                track.docs_url
                if track and track.docs_url
                else product.definition.docs_url
            ),
            can_reply=permissions.can_reply_ticket(request.user, ticket),
            can_close=ticket.status != Status.CLOSED
            and permissions.can_close_ticket(request.user, ticket),
            can_change_priority=permissions.can_change_ticket_priority(
                request.user,
                ticket,
            ),
            # In the order the integrator chose from when filing.
            priorities=SupportForm.base_fields["priority"].choices,
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
