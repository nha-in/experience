import csv
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_safe

from . import permissions
from . import production
from . import views
from .forms import ProductionAccessForm
from .models import ProductCredential
from .models import ProductWorkspace
from .registry import get_program

PAGE_SIZE = 20


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

    A `tab` naming one of the register's old views still opens the register,
    which is where this screen published those links before it had stages.
    """
    stage = request.GET.get("tab", "pending")
    if stage in production.TABS:
        stage = "approved"
    if stage not in production.STAGES:
        stage = "pending"
    return stage, request.GET.get("q", "").strip()[:100]


@login_required
@never_cache
@require_safe
def production_list(request):
    program = _program(request.user)
    stage, q = _filters(request)
    approved, counts = production.listing(program, q=q)
    waiting, pending_counts = production.pending(program, request.user, q=q)
    counts |= pending_counts
    page = Paginator(
        waiting if stage == "pending" else approved,
        PAGE_SIZE,
    ).get_page(request.GET.get("page"))
    return render(
        request,
        "experiences/production_list.html",
        {
            "nav": "production",
            "page_title": "Production Approval",
            "page": page,
            "rows": [] if stage == "pending" else production.with_codes(page),
            "requests": views.waiting_rows(page) if stage == "pending" else [],
            "stage": stage,
            "q": q,
            "counts": counts,
            "can_manage": production.can_manage(request.user, program),
            "stages": [
                ("pending", "Pending", counts["pending"]),
                ("approved", "Approved", counts["all"]),
            ],
            "filter_query": urlencode({"tab": stage, "q": q}),
        },
    )


@login_required
@never_cache
@require_safe
def production_export(request):
    """The approved register, which is the only stage that is a record."""
    program = _program(request.user)
    _stage, q = _filters(request)
    query, _counts = production.listing(program, q=q)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"production-approved-{timezone.localdate():%Y-%m-%d}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # Lets spreadsheet software pick UTF-8 for organisation names.
    response.write("﻿")
    csv.writer(response).writerows(production.csv_rows(query))
    return response


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def production_detail(request, reference):
    program = _program(request.user)
    workspace = get_object_or_404(
        ProductWorkspace.objects.select_related("product__organisation"),
        reference=reference,
        experience_type=program.key,
    )
    product = workspace.product
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
            "reference": workspace.reference,
            "sandbox": ProductCredential.objects.filter(product=product).first(),
            "exits": exits,
            "eligible": bool(exits),
            "can_manage": can_manage,
            "form": form,
            "history": production.history(product),
        },
    )
