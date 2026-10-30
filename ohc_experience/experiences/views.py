import math
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
from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import IntegerField
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import NullIf
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
from ohc_experience.organisations.models import GOVERNMENT
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
from .models import OUTCOME_EXPIRING_DAYS
from .models import AuditEvent
from .models import EventRegistration
from .models import Product
from .models import ProductCredential
from .models import ProductOutcomeStatus
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
from .queue_presentation import ACTED_ON
from .queue_presentation import QUEUE_SORTS
from .queue_presentation import VERIFICATION_SORTS
from .queue_presentation import grouped_requests
from .queue_presentation import ordered_verifications
from .queue_presentation import populate_queue_page
from .queue_presentation import populate_verification_page
from .queue_presentation import queue_requests
from .queue_presentation import review_order
from .queue_presentation import verification_requests
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
            "wasa": "certificate_valid_until",
        }
    return columns


#: The Products list's WASA filter, which the dashboard's WASA cards open.
CERTIFICATE_STANDINGS = [
    ("expired", "Expired"),
    ("expiring", f"Expiring in {OUTCOME_EXPIRING_DAYS} days"),
    ("active", "Active"),
]


def _certificate_standing(standing):
    """Products whose current certificate reads as this on the product page's
    WASA card. A revoked or undated certificate is none of them."""
    today = timezone.localdate()
    soon = today + timedelta(days=OUTCOME_EXPIRING_DAYS)
    active = Q(certificate_status=ProductOutcomeStatus.ACTIVE)
    return {
        "expired": ~Q(certificate_status=ProductOutcomeStatus.REVOKED)
        & (
            Q(certificate_valid_until__lt=today)
            | Q(
                certificate_status=ProductOutcomeStatus.EXPIRED,
                certificate_valid_until__isnull=False,
            )
        ),
        "expiring": active & Q(certificate_valid_until__range=(today, soon)),
        "active": active & Q(certificate_valid_until__gt=soon),
    }[standing]


PRODUCT_EXPORT_HEADER = (
    "Reference",
    "Sandbox client ID",
    "Production client ID",
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


def _client_id(product):
    credential = getattr(product, "credential", None)
    return credential.client_id if credential else ""


def _product_export_rows(products, *, open_counts=False):
    for product in products.select_related("credential"):
        yield (
            product.reference,
            _client_id(product),
            product.production_client_id,
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
    sort, order = tables.sorting(request, _product_columns(), "-registered")
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
    "Product",
    "Sandbox client ID",
    "Production client ID",
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
    """A row per product, the organisation repeated; one row when it has none."""
    for organisation in _with_labels(organisations):
        details = (
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
        for product in organisation.export_products or [None]:
            yield (
                *details,
                product.name if product else "",
                _client_id(product) if product else "",
                product.production_client_id if product else "",
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
    sort, order = tables.sorting(request, _organisation_columns(), "-registered")
    rows = rows.order_by(*order)
    if export := tables.export_format(request):
        products = (
            permissions.visible_products(request.user)
            .select_related("credential")
            .order_by("name", "pk")
        )
        return tables.export_response(
            export,
            "organisations",
            ORGANISATION_EXPORT_HEADER,
            _organisation_export_rows(
                rows.prefetch_related(
                    Prefetch("products", queryset=products, to_attr="export_products"),
                ),
            ),
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
    program = get_program()
    rows = program.with_certificate(_product_rows(request.user))
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
    track_choices = permissions.allowed_tracks(request.user)
    track = request.GET.get("track", "")
    if track in {choice.code for choice in track_choices}:
        rows = rows.filter(
            permissions.track_products(program.track_map()[track]),
        )
    else:
        track = ""
    wasa_choices = CERTIFICATE_STANDINGS if program.applications.certification else []
    wasa = request.GET.get("wasa", "")
    if wasa in dict(wasa_choices):
        rows = rows.filter(_certificate_standing(wasa))
    else:
        wasa = ""
    search = request.GET.get("q", "").strip()
    if search:
        rows = rows.filter(
            Q(name__icontains=search) | Q(reference__icontains=search),
        )
    sort, order = tables.sorting(
        request,
        _product_columns(reviewer=True),
        "-registered",
    )
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
            track_choices=track_choices,
            selected_track=track,
            wasa_choices=wasa_choices,
            selected_wasa=wasa,
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


def _with_open_questions(requests):
    """When each request's latest open question was asked, or None."""
    return requests.annotate(
        question_asked_at=Max(
            "queries__raised_at",
            filter=Q(
                queries__submission_id=F("selected_submission_id"),
                queries__status="open",
            ),
        ),
    )


def _request_group(item):
    """Whose move a request is: the reviewer's, the integrator's, or nobody's.

    A request under review that waits on a prerequisite is marked with
    `held_by`, and is the integrator's when every prerequisite is theirs.
    """
    if item.status == ReviewItem.Status.APPROVED:
        return "finished"
    if item.status != ReviewItem.Status.IN_REVIEW:
        return "theirs"
    held = services.pending_prerequisites(item)
    if not held:
        return "yours"
    names = [_prerequisite_label(item.program, waits_on)[0] for waits_on in held]
    item.held_by = f"{readable_list(names)} {'is' if len(names) == 1 else 'are'}"
    # Only a prerequisite under review is the reviewer's: a reply to every
    # question puts it back there.
    if any(
        waits_on.review and waits_on.review.status == ReviewItem.Status.IN_REVIEW
        for waits_on in held
    ):
        return "yours"
    return "theirs"


def _request_groups(items):
    groups = {"yours": [], "theirs": [], "finished": []}
    for item in items:
        groups[_request_group(item)].append(item)
    return groups


def _wasa_notice(certification):
    """An expired or soon-expiring certificate, with its days counted, else None."""
    if certification.get("status") not in ("expired", "expiring"):
        return None
    days = (certification["valid_until"] - timezone.localdate()).days
    return certification | {"days": abs(days)}


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
        .select_related("application__milestone", "selected_submission")
    )
    verification = _with_open_questions(
        visible_reviews.filter(kind=ReviewItem.Kind.ORGANISATION),
    ).first()
    withdrawn = (
        verification
        and organization.verification_status
        == Organisation.VerificationStatus.WITHDRAWN
    )
    # A draft is the integrator's unsent work, not yet a request.
    requests = _with_open_questions(
        visible_reviews.exclude(status="draft").exclude(
            kind=ReviewItem.Kind.ORGANISATION,
        ),
    ).order_by("pk")
    by_product = {}
    for item in requests:
        by_product.setdefault(item.product_id, []).append(item)
    products = list(_products(request.user).filter(organisation=organization))
    for product in products:
        product.requests = _request_groups(by_product.get(product.pk, []))
        product.wasa = _wasa_notice(_certification_context(request, product))
    products.sort(key=lambda product: (not product.requests["yours"], product.name))
    if verification:
        verification.group = _request_group(verification)
    # Verification is reviewed on a product page, so it waits for the first product.
    awaits_product = not organization.products.exists()
    wasa = Counter(product.wasa["status"] for product in products if product.wasa)
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
            verification=verification,
            withdrawn_verification=verification if withdrawn else None,
            withdrawn_at=services.withdrawn_at(verification) if withdrawn else None,
            verification_awaits_product=awaits_product,
            products=products,
            waiting_count=sum(len(product.requests["yours"]) for product in products)
            + (getattr(verification, "group", "") == "yours"),
            wasa_expired=wasa["expired"],
            wasa_expiring=wasa["expiring"],
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
    """The question boxes a request's card on the product page opens.

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
        asked = request.method == "GET" and request.GET.get("review") == str(item.pk)
        action = request.POST.get("action") if posted else None
        if asked:
            action = request.GET.get("action")
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
                "query_drafts": _query_drafts(request, item, action)
                if posted or asked
                else {},
                "opened": posted or asked,
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
            status__in=["in_review", "query_raised"],
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
            nav="queue",
            experience_program=program,
            # _context keeps `product` for an integrator's own product.
            staff_product=product,
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
def product_overview(request, reference):
    """A product at a glance; its decisions stay on the product review page."""
    _reviewer_required(request)
    product = get_object_or_404(_products(request.user), reference=reference)
    program = product.definition
    visible_items = permissions.visible_reviews(request.user)
    items = _with_open_questions(
        visible_items.filter(_product_review_scope(product))
        .exclude(kind=ReviewItem.Kind.PRODUCT)
        .filter(permissions.shown_to_reviewers())
        .select_related("application__milestone"),
    ).order_by("pk")
    general_access = permissions.has_access(request.user, "review", program=program.key)
    credential = (
        ProductCredential.objects.filter(product=product).first()
        if general_access
        else None
    )
    provisioning = provisioning_progress(product) if general_access else []
    activity = AuditEvent.objects.filter(
        Q(product=product)
        | Q(item__in=[item for item in items if not item.product_id]),
        item__in=visible_items,
    )
    certification = _certification_context(request, product)
    return render(
        request,
        "experiences/product_overview.html",
        _context(
            request,
            page_title=product.name,
            nav="organizations",
            experience_program=program,
            # _context keeps `product` for an integrator's own product.
            staff_product=product,
            organisation=product.organisation,
            requests=_request_groups(items),
            certification=certification,
            wasa=_wasa_notice(certification),
            general_access=general_access,
            credential=credential,
            provisioning=provisioning,
            provisioning_ready=all(
                system.variant == "success" for system in provisioning
            ),
            can_manage_credentials=_can_provision(request.user, product, program.key)
            or _can_revoke_credentials(request.user, credential)
            or _can_retry_deprovisioning(request.user, product, credential)
            or _can_reprovision(request.user, product, credential),
            open_tickets=permissions.visible_tickets(request.user).filter(
                product=product,
                status__in=["open", "awaiting_integrator"],
            )[:5],
            activity=activity.select_related("actor", "item")[:10],
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
        return redirect("experiences:product-overview", reference=reference)
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
        if reviewer and item.unresolved_query_count:
            item.portal_url = item.portal_url.replace(
                f"#review-{item.pk}",
                f"#queries-{item.pk}",
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
@never_cache
@require_http_methods(["GET", "POST"])
def reference_environment(request, reference):
    product = _product(request, reference)
    environment = product.definition.reference_environment
    if environment is None:
        raise Http404
    permissions.require_integrator(request.user, product.organisation)
    credential = ProductCredential.objects.filter(product=product).first()
    client_secret = ""
    if request.method == "POST":
        if not credential:
            raise Http404
        # Fill in secret reveals the secret as Credentials does, so it is audited
        # and rate limited the same way.
        try:
            client_secret = credential_services.reveal(credential, request.user)
        except ValidationError as error:
            _error(request, error)
            # A credential revoked meanwhile no longer offers the button.
            credential.refresh_from_db()
            if not request.htmx:
                return redirect(
                    "experiences:reference-environment",
                    reference=reference,
                )
        else:
            messages.success(request, "Client secret filled in.")
    if request.method == "POST" and request.htmx and not request.htmx.boosted:
        template = "experiences/partials/reference_secret_swap.html"
        context = {
            "credential": credential,
            "reference_environment": environment,
            "reference_client_secret": client_secret,
        }
    else:
        template = "experiences/reference_environment.html"
        flows = [
            (
                product.definition.milestones[key],
                names,
                key in environment.in_progress,
            )
            for key, names in environment.flows.items()
        ]
        client_id = credential.client_id if credential else ""
        shells = [
            (key, shell, environment.command_segments(shell, client_id, client_secret))
            for key, shell in environment.shells.items()
        ]
        context = _context(
            request,
            product,
            nav="reference",
            page_title="Reference environment",
            reference_environment=environment,
            reference_milestones=environment.milestone_rows(product.definition),
            reference_flows=flows,
            reference_shells=shells,
            reference_client_id=client_id,
            reference_client_secret=client_secret,
            credential=credential,
            has_credential=credential is not None,
        )
    response = render(request, template, context)
    if client_secret:
        # A revealed secret leaves no copy in a cache or a shared proxy.
        response["Cache-Control"] = "no-store, private"
        response["Vary"] = "Cookie"
    return response


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
    """Requests offered in the Products list's Type filter, to reviewers who can
    see them. Organisation verification has a list of its own."""
    if not permissions.has_access(user, "review", program=program.key):
        return []
    return [
        (value, label)
        for value, (label, _query) in _requests(program).items()
        if value != ReviewItem.Kind.ORGANISATION
    ]


def _programme_filter(code):
    """Requests on a product in this programme, and the verification of every
    organisation that owns one, so an organisation counts in each programme."""
    products = Product.objects.filter(
        permissions.track_products(get_program().track_map()[code]),
    )
    return Q(product__in=products) | Q(
        kind=ReviewItem.Kind.ORGANISATION,
        organisation__in=products.values("organisation"),
    )


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


def _queue_tile(user, label, **params):
    """A link into the queue, and how many rows it opens there."""
    kind = params.get("kind", "products")
    query, scopes = _queue_list(kind, user, params)
    if params["scope"] != "all":
        query = query.filter(scopes[params["scope"]])
    return {
        "label": label,
        "count": _queue_count(kind, query),
        "url": _queue_url(**params),
    }


def _dashboard_queues(user, program, track):
    """Where a programme's pending work waits, or every programme's without a
    track: entity registrations and WASA renewals for onboarding reviewers,
    and exits for anyone the track's reviews are open to."""
    narrow = {"track": track.code} if track else {}
    queues = {"exit": {"scope": "ready", "item": track.code if track else "milestones"}}
    if permissions.has_access(user, "review", program=program.key):
        queues["onboarding"] = {"kind": "organisations", "scope": "ready", **narrow}
        if program.applications.certification:
            queues["renewals"] = {"scope": "ready", "item": "certification", **narrow}
    return queues


def _queue_split(user, params):
    """A queue's Pending rows, the new ones, and those back with a reply."""
    new = _queue_tile(user, QUEUE_NEW[1], status=QUEUE_NEW[0], **params)
    answered = _queue_tile(user, QUEUE_ANSWERED[1], status=QUEUE_ANSWERED[0], **params)
    both = new["count"] + answered["count"]
    return {
        "pending": _queue_tile(user, "Pending", **params),
        "new": new,
        "answered": answered,
        "tiles": [new, answered],
        "new_share": round(new["count"] / both * 100) if both else 0,
        "answered_share": round(answered["count"] / both * 100) if both else 0,
    }


def _wasa_standings(user, program, track):
    """Products by their current WASA, each split by who runs them."""
    products = program.with_certificate(_products(user))
    if track:
        products = products.filter(permissions.track_products(track))
    standings = {}
    for standing, label in CERTIFICATE_STANDINGS:
        counts = products.filter(_certificate_standing(standing)).aggregate(
            total=Count("pk"),
            government=Count("pk", filter=Q(organisation__entity_type=GOVERNMENT)),
        )
        query = {"wasa": standing, "sort": "wasa"} | (
            {"track": track.code} if track else {}
        )
        standings[standing] = {
            "label": label,
            "variant": STANDING_VARIANTS[standing],
            "count": counts["total"],
            "government": counts["government"],
            "private": counts["total"] - counts["government"],
            "url": f"{reverse('experiences:products')}?{urlencode(query)}",
        }
    return standings


#: The dot beside each standing, as the product page's WASA card tones it.
STANDING_VARIANTS = {"expired": "warning", "expiring": "neutral", "active": "success"}


#: The compliance ring's radius, in the 92px box it is drawn in.
RING_RADIUS = 34


def _compliance(standings):
    live = sum(standing["count"] for standing in standings.values())
    percent = round(standings["active"]["count"] / live * 100) if live else 0
    circumference = 2 * math.pi * RING_RADIUS
    return {
        "live": live,
        "percent": percent,
        "radius": RING_RADIUS,
        "dash": f"{circumference * percent / 100:.1f} {circumference:.1f}",
    }


def _attention_cards(splits, standings):
    """The figures that need a reviewer, highest impact first."""
    cards = []
    if standings:
        cards += [
            standings["expired"]
            | {
                "label": "WASA expired",
                "note": "Live products without a valid WASA.",
                "action": "View products",
                "variant": "warning",
            },
            standings["expiring"]
            | {
                "label": f"WASA expiring in {OUTCOME_EXPIRING_DAYS} days",
                "note": "Owners get automatic reminders.",
                "action": "View products",
                "variant": "neutral",
            },
        ]
    onboarding, exit_ = splits.get("onboarding"), splits["exit"]
    if onboarding:
        cards.append(
            onboarding["new"]
            | {
                "label": "New applications to verify",
                "note": "Entity registrations awaiting verification.",
                "action": "Start verification",
            },
        )
    replies = exit_["answered"]["count"]
    note = "Applicants answered your query."
    if onboarding:
        replies += onboarding["answered"]["count"]
        note = (
            f"Entities {onboarding['answered']['count']} · "
            f"Exits {exit_['answered']['count']}"
        )
    cards += [
        exit_["answered"]
        | {
            "label": "Clarifications received",
            "count": replies,
            "note": note,
            "action": "Review responses",
        },
        exit_["new"]
        | {
            "label": "Exit requests to review",
            "note": "New milestone requests.",
            "action": "Review exits",
        },
    ]
    return cards


@login_required
def assess_dashboard(request):
    _reviewer_required(request)
    user, program = request.user, get_program()
    tracks = permissions.allowed_tracks(user)
    track = next(
        (track for track in tracks if track.code == request.GET.get("track")),
        None,
    )

    def total(track):
        return sum(
            _queue_tile(user, "", **params)["count"]
            for params in _dashboard_queues(user, program, track).values()
        )

    programmes = [{"code": "", "label": "All", "total": total(None)}] + [
        {"code": choice.code, "label": choice.code, "total": total(choice)}
        for choice in tracks
    ]
    busiest = max(programme["total"] for programme in programmes[1:]) if tracks else 0
    for programme in programmes:
        programme["url"] = "?" + urlencode({"track": programme["code"]})
        programme["width"] = round(programme["total"] / busiest * 100) if busiest else 0
    queues = _dashboard_queues(user, program, track)
    pending = {
        name: _queue_tile(user, "", **params)["count"]
        for name, params in queues.items()
    }
    splits = {
        name: _queue_split(user, params)
        for name, params in queues.items()
        if name != "renewals"
    }
    standings = _wasa_standings(user, program, track) if "renewals" in queues else None
    context = _context(
        request,
        # Superusers administer the portal; Staff & permissions is theirs alone.
        page_title="Administrator dashboard"
        if user.is_superuser
        else "Reviewer dashboard",
        nav="assess-dashboard",
        programme=track.code if track else "",
        programmes=programmes,
        total=sum(pending.values()),
        pending=pending,
        queue_url=_queue_url(scope="ready", **({"item": track.code} if track else {})),
        renewals_url=_queue_url(**queues["renewals"]) if standings else "",
        onboarding_queue=splits.get("onboarding"),
        exit=splits["exit"],
        standings=standings,
        compliance=_compliance(standings) if standings else None,
        attention_cards=_attention_cards(splits, standings),
    )
    return render(request, "experiences/assess_dashboard.html", context)


# The queue's sort once took these names, which saved links still carry.
QUEUE_SORT_ALIASES = {"newest": "-submitted", "oldest": "submitted"}


def _queue_sort(request, kind="products"):
    sort = request.GET.get("sort", "")
    sort = QUEUE_SORT_ALIASES.get(sort, sort)
    sorts = VERIFICATION_SORTS if kind == "organisations" else QUEUE_SORTS
    return sort if sort in sorts else "-submitted"


REVIEW_REFERENCE = re.compile(r"REV-?(\d{1,9})", re.IGNORECASE)


def _queue_search(search):
    """Requests whose product, organisation or reference matches the search."""
    matches = (
        Q(product__name__icontains=search)
        | Q(product__reference__icontains=search)
        | Q(organisation__name__icontains=search)
        | Q(organisation__legal_name__icontains=search)
        | Q(application__reference__icontains=search)
    )
    reference = REVIEW_REFERENCE.fullmatch(search)
    if reference:
        matches |= Q(pk=int(reference[1]))
    return matches


def _verification_search(search, user):
    """Verifications whose organisation, reference or one of its products matches.

    A product's name finds its organisation, so a search carried over from the
    Products list still leads to the verification its milestones wait on.
    """
    products = permissions.visible_products(user).filter(
        Q(name__icontains=search) | Q(reference__icontains=search),
    )
    matches = (
        Q(organisation__name__icontains=search)
        | Q(organisation__legal_name__icontains=search)
        | Q(organisation__in=products.values("organisation"))
    )
    reference = REVIEW_REFERENCE.fullmatch(search)
    if reference:
        matches |= Q(pk=int(reference[1]))
    return matches


def _prerequisite_label(program, prerequisite):
    """("M1", "under review"), or ("organisation verification", "query raised")."""
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


def _queue_rows(page, user, matching, tab="all"):
    """The page's entries, each request marked with what it waits on and its chip."""
    page = populate_queue_page(page, user, matching)
    for entry in page:
        entry.tab = tab
    for item in waiting_rows(item for entry in page for item in entry.reviews):
        held = item.waiting_on and item.status == ReviewItem.Status.IN_REVIEW
        item.queue_state = "blocked" if held else item.status
    return page


#: Not a stored status: a request under review shows as waiting for as long as
#: a prerequisite holds it.
QUEUE_WAITING = ("waiting", "Waiting on prerequisites")


def _status_choice(status):
    return status.value, status.label


#: Pending's Status filter: a request is new until the integrator answers a
#: query on it, and then waits on the reviewer to read the reply.
QUEUE_NEW = ("new", "New")
QUEUE_ANSWERED = ("answered", "Clarification received")


#: Every state a chip shows, in the order a request moves through them.
QUEUE_STATUSES = [
    _status_choice(ReviewItem.Status.IN_REVIEW),
    QUEUE_WAITING,
    *(_status_choice(status) for status in ACTED_ON),
]


def _queue_statuses(scope, kind="products"):
    """The Status filter's choices: the states this tab's chips show.

    Pending holds only what a reviewer can decide now, which is all under
    review, so it tells new requests from those the integrator has answered a
    query on. Done holds everything else. An organisation's verification waits
    on nothing, so the Organisations list has no Waiting on prerequisites.
    """
    if scope == "ready":
        return [QUEUE_NEW, QUEUE_ANSWERED]
    return [
        choice
        for choice in QUEUE_STATUSES
        if not (scope == "decided" and choice[0] == ReviewItem.Status.IN_REVIEW)
        and not (kind == "organisations" and choice == QUEUE_WAITING)
    ]


def _queue_scopes(query, visible):
    """Each tab's requests among the Products list's.

    A product is pending while a reviewer can decide one of its requests now
    and none of the requests the reviewer can see has an open query; filters
    narrow what a tab lists, not whether a product waits on the integrator.
    Every other product is done, so the two tabs add up to All.
    """
    actionable = services.actionable_reviews()
    queried = visible.filter(status=ReviewItem.Status.QUERY)
    pending_products = (
        query.filter(actionable)
        .exclude(product_id__in=queried.values("product_id"))
        .values("product_id")
    )
    pending = Q(product_id__in=pending_products)
    return {
        "ready": actionable & pending,
        # Every request the filters keep, so a product an open query sets aside
        # is still listed when its query falls outside them. Its row leaves out
        # the requests the query sets aside.
        "decided": ~pending,
    }


def _verification_scopes():
    """Each tab's verifications: Pending what a reviewer can decide now, and Done
    those waiting on the integrator's reply or already decided."""
    actionable = services.actionable_reviews()
    return {"ready": actionable, "decided": ~actionable}


def _queue_status_filter(status):
    """The requests whose chip shows this state, or that Pending's filter names."""
    if status in (QUEUE_NEW[0], QUEUE_ANSWERED[0]):
        answered = Q(
            pk__in=ReviewQuery.objects.filter(
                status="answered",
                submission=F("item__selected_submission"),
            ).values("item"),
        )
        if status == QUEUE_NEW[0]:
            answered = ~answered
        return Q(status=ReviewItem.Status.IN_REVIEW) & answered
    if status == QUEUE_WAITING[0]:
        return Q(status=ReviewItem.Status.IN_REVIEW) & services.waiting_reviews()
    if status == ReviewItem.Status.IN_REVIEW:
        return Q(status=status) & ~services.waiting_reviews()
    return Q(status=status)


QUEUE_EXPORT_HEADER = (
    "Reference",
    "Product",
    "Organisation",
    "Requests",
    "Approved",
    "Submitted on",
    "Age (days)",
)

VERIFICATION_EXPORT_HEADER = (
    "Reference",
    "Organisation",
    "Type of entity",
    "State",
    "Status",
    "Products",
    "Requests waiting",
    "Submitted on",
    "Age (days)",
)


def _queue_request_label(item):
    """A request as its chip reads: "M2 Under review, waiting on M1"."""
    if item.queue_milestone:
        name = item.queue_milestone.code
    else:
        name = item.application.reference
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
        )


def _verification_rows(page, user):
    """The page's verifications, each organisation labelled as the table reads it."""
    page = populate_verification_page(page, user)
    list(_with_labels(entry.organisation for entry in page))
    return page


def _verification_export_rows(query, user):
    """Every verification the filters match, built as one page of the list."""
    entries = _verification_rows(
        Paginator(query, max(query.count(), 1)).page(1),
        user,
    )
    for entry in entries:
        yield (
            entry.reference,
            entry.organisation.display_name,
            entry.organisation.entity_type_label,
            entry.organisation.state_label,
            entry.status_label,
            ", ".join(product.name for product in entry.products),
            entry.held,
            tables.day(entry.submitted_at),
            entry.age,
        )


#: The queue's two lists, behind one switch, each with its label and icon.
#: Products comes first and is the default, so its links leave it unnamed.
QUEUE_KINDS = {
    "products": ("Products", "track"),
    "organisations": ("Organisations", "organisation"),
}


def _queue_list(kind, user, params):
    """A list's requests under every filter but its tabs', and each tab's filter.

    Search and status hold in both lists; the Type filter is the
    Products list's alone. Each narrows the requests before the tabs split
    them, so every tab counts what the filters match, and All stays Pending
    plus Done: a product is pending when a request the filters keep can be
    decided now.
    """
    if kind == "organisations":
        query = verification_requests(user)
    else:
        query = visible = queue_requests(user)
    if params.get("status"):
        query = query.filter(_queue_status_filter(params["status"]))
    if params.get("track"):
        query = query.filter(_programme_filter(params["track"]))
    search = params.get("q", "").strip()
    if kind == "organisations":
        if search:
            query = query.filter(_verification_search(search, user))
        return query, _verification_scopes()
    item_filter = _item_filter(get_program(), params.get("item", ""))
    if item_filter is not None:
        query = query.filter(item_filter)
    if search:
        query = query.filter(_queue_search(search))
    return query, _queue_scopes(query, visible)


def _queue_count(kind, query):
    """How many rows a list shows: one a verification, or one a product."""
    if kind == "organisations":
        return query.count()
    return grouped_requests(query).count()


def _kind_switch(params, kind):
    """Both lists: this one as it is filtered, and the other on the same tab
    with the same search, the filter both lists have."""
    shared = {"scope": params["scope"]}
    for name in ("q", "track"):
        if params.get(name):
            shared[name] = params[name]
    switch = []
    for value, (label, icon) in QUEUE_KINDS.items():
        if value == kind:
            url = f"?{params.urlencode()}"
        else:
            link = shared if value == "products" else {"kind": value, **shared}
            url = f"?{urlencode(link)}"
        switch.append({"kind": value, "label": label, "icon": icon, "url": url})
    return switch


@login_required
def queue(request):
    _reviewer_required(request)
    program = get_program()
    verifies = permissions.has_access(request.user, "review", program=program.key)
    params = request.GET.copy()
    params.pop("page", None)
    if verifies and params.get("item") == ReviewItem.Kind.ORGANISATION:
        # Links from the dashboard's old Sandbox access card ask the Type filter
        # for verifications, which have a list of their own.
        params.pop("item")
        params["kind"] = "organisations"
        return redirect(f"{reverse('experiences:queue')}?{params.urlencode()}")
    kind = params.get("kind", "")
    if verifies and kind == "organisations":
        params["kind"] = kind
    else:
        kind = "products"
        params.pop("kind", None)
    scope = params.get("scope", "")
    if scope not in ("ready", "decided", "all"):
        scope = "ready"
    params["scope"] = scope
    # A tab drops a status its Status filter does not offer.
    statuses = _queue_statuses(scope, kind)
    if params.get("status", "") not in dict(statuses):
        params.pop("status", None)
    tracks = permissions.allowed_tracks(request.user)
    if params.get("track") not in {track.code for track in tracks}:
        params.pop("track", None)
    query, scopes = _queue_list(kind, request.user, params)
    stage_counts = {
        stage: _queue_count(kind, query.filter(stage_filter))
        for stage, stage_filter in scopes.items()
    }
    stage_counts["all"] = _queue_count(kind, query)
    if scope != "all":
        query = query.filter(scopes[scope])
    sort = _queue_sort(request, kind)
    if export := tables.export_format(request):
        if kind == "organisations":
            return tables.export_response(
                export,
                "review-queue-organisations",
                VERIFICATION_EXPORT_HEADER,
                _verification_export_rows(
                    ordered_verifications(query, sort),
                    request.user,
                ),
            )
        return tables.export_response(
            export,
            "review-queue",
            QUEUE_EXPORT_HEADER,
            _queue_export_rows(grouped_requests(query, sort), request.user, query),
        )
    if kind == "organisations":
        page = _verification_rows(
            _page(request, ordered_verifications(query, sort)),
            request.user,
        )
    else:
        page = _queue_rows(
            _page(request, grouped_requests(query, sort)),
            request.user,
            query,
            scope,
        )
    return render(
        request,
        "experiences/queue.html",
        _context(
            request,
            page_title="Review queue",
            nav="queue",
            page=page,
            queue_kind=kind,
            # What the list's own links add to name it: nothing, for Products.
            kind_query="kind=organisations&" if kind == "organisations" else "",
            kind_switch=_kind_switch(params, kind) if verifies else [],
            stage_counts=stage_counts,
            queue_scope=scope,
            queue_sort=sort,
            table_sort=sort,
            statuses=statuses,
            filters=params,
            filter_query=params.urlencode(),
            request_choices=_request_choices(program, request.user),
            track_choices=tracks,
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


def _provision(request, product):
    """Start or re-run the chain, and say which of the two it was."""
    started = awaiting_provisioning(product)
    start_provisioning(product, started_by=request.user)
    messages.success(
        request,
        "Provisioning started." if started else "Provisioning restarted.",
    )


@login_required
def open_record(request, pk):
    """One link for an email: each reader lands on the page their role can open."""
    item = _item(request, pk)
    if permissions.reviewer(request.user):
        return redirect(item.get_absolute_url())
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
            f"sessions-{period}",
            (*EVENT_EXPORT_HEADER, "Registrations" if manage else "Registered"),
            _event_export_rows(shown, registered=registered, manage=manage),
        )
    return render(
        request,
        "experiences/events.html",
        _context(
            request,
            product,
            page_title="Sessions and Activities",
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
