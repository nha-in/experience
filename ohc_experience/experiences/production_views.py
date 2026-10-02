from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_safe

from . import permissions
from . import production
from . import tables
from .forms import ProductionAccessForm
from .models import Product
from .models import ProductCredential
from .registry import get_program

PAGE_SIZE = 20

COLUMNS = {
    "product": Lower("name"),
    "organisation": Lower(tables.organisation_name("organisation__")),
    "approved": "first_exit_at",
    "client_id": "production_client_id",
    "issued": "production_issued_on",
}

# Each stage opens on what NHA is most overdue on, or most recently did.
DEFAULT_SORT = {"pending": "approved", "approved": "-issued"}


def _program(user):
    """The portal's program, when it keeps production IDs and the user may see them.

    Only General/onboarding reviewers qualify: track-only reviewers pass the
    wider review-area check but see nothing of a product's registration.
    """
    program = get_program()
    if not production.enabled(program):
        raise Http404
    if not production.can_view(user, program):
        msg = "Production details are for onboarding reviewers."
        raise PermissionDenied(msg)
    return program


def _filters(request):
    """The stage of production approval on show.

    A `tab` naming one of the register's older views still opens the stage that
    now holds those products, which is where this screen published its links.
    """
    stage = request.GET.get("tab", "pending")
    stage = production.TABS.get(stage, stage)
    if stage not in production.STAGES:
        stage = "pending"
    return stage, request.GET.get("q", "").strip()[:100]


@login_required
@never_cache
@require_safe
def production_list(request):
    program = _program(request.user)
    stage, q = _filters(request)
    products, counts = production.listing(program, stage=stage, q=q)
    sort, order = tables.sorting(request, COLUMNS, DEFAULT_SORT[stage])
    page = tables.paginate(request, products.order_by(*order), PAGE_SIZE)
    return render(
        request,
        "experiences/production_list.html",
        {
            "nav": "production",
            "page_title": "Production approval",
            "page": page,
            "rows": production.with_codes(page),
            "stage": stage,
            "q": q,
            "counts": counts,
            "can_manage": production.can_manage(request.user, program),
            "stages": [
                ("pending", "Pending", counts["pending"]),
                ("approved", "Approved", counts["approved"]),
            ],
            "table_sort": sort,
            "filter_query": urlencode({"tab": stage, "q": q, "sort": sort}),
        },
    )


@login_required
@never_cache
@require_safe
def production_export(request):
    """The whole register, both stages, since a pending row is a record too."""
    program = _program(request.user)
    _stage, q = _filters(request)
    query, _counts = production.listing(program, q=q)
    if "sort" in request.GET:
        query = query.order_by(*tables.sorting(request, COLUMNS, "-approved")[1])
    return tables.export_response(
        tables.export_format(request) or "csv",
        "production-approved",
        production.CSV_HEADER,
        production.export_rows(query),
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def production_detail(request, reference):
    program = _program(request.user)
    product = get_object_or_404(
        Product.objects.select_related("organisation"),
        reference=reference,
        experience_type=program.key,
    )
    can_manage = production.can_manage(request.user, program)
    current_id = product.production_client_id
    form = ProductionAccessForm(
        initial={
            "client_id": current_id,
            # An ID being added today was, as a rule, issued today; a correction
            # opens on the day already saved against it.
            "issued_on": product.production_issued_on or timezone.localdate(),
            "expected": current_id,
        },
    )
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        intent = request.POST.get("intent")
        form = ProductionAccessForm(request.POST)
        try:
            if intent == "remove":
                production.remove(
                    product,
                    request.user,
                    expected=request.POST.get("expected", ""),
                )
                messages.success(request, "Production details removed.")
                return redirect("experiences:production-detail", reference=reference)
            if intent != "save":
                msg = "Choose a valid action."
                raise ValidationError(msg)  # noqa: TRY301
            if form.is_valid():
                production.record(
                    product,
                    request.user,
                    client_id=form.cleaned_data["client_id"],
                    issued_on=form.cleaned_data["issued_on"],
                    expected=form.cleaned_data["expected"],
                )
                messages.success(request, "Production details saved.")
                return redirect("experiences:production-detail", reference=reference)
        except ValidationError as error:
            form.add_error(None, error)
    exits = production.approved_exits(product)
    reviews = {
        item.application_id: item
        for item in permissions.visible_reviews(request.user).filter(
            application__in=[milestone.application for milestone in exits],
        )
    }
    for milestone in exits:
        milestone.review = reviews.get(milestone.application_id)
    return render(
        request,
        "experiences/production_detail.html",
        {
            "nav": "production",
            "page_title": f"{product.name} · Production details",
            "product": product,
            "reference": product.reference,
            "sandbox": ProductCredential.objects.filter(product=product).first(),
            "exits": exits,
            "eligible": bool(exits),
            "can_manage": can_manage,
            "form": form,
            "history": production.history(product),
        },
    )
