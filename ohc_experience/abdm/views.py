"""Integrator screens: onboarding, products, credentials, tracks and queries.

Every view is scoped through ``OrganisationMixin`` — a sandbox id that belongs
to another organisation is simply not in the queryset and 404s. Writes go
through ``services``; the views only collect input and render.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView
from django.views.generic import TemplateView
from django_htmx.http import HttpResponseClientRedirect

from ohc_experience.events.selectors import dashboard_events
from ohc_experience.events.selectors import registered_event_ids
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.views import OrganisationMixin
from ohc_experience.support.models import TicketMessage
from ohc_experience.users.permissions import is_ohc_team

from . import services
from . import tracks
from .context_processors import SESSION_PRODUCT_KEY
from .forms import CallbackUrlsForm
from .forms import ExitRequestForm
from .forms import ProductForm
from .forms import QueryReplyForm
from .models import ComplianceRecord
from .models import ReviewItem
from .models import ReviewQuery
from .selectors import activity_for_product
from .selectors import can_write
from .selectors import products_for
from .selectors import query_thread_context
from .selectors import track_summaries

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

# kind → (model, downloadable fields, how to reach the owning organisation)
DOCUMENT_KINDS = {
    "organisation": (
        Organisation,
        frozenset({"logo", "verification_document"}),
        lambda obj: obj,
    ),
    "compliance": (
        ComplianceRecord,
        frozenset({"functional_certificate", "functional_report"}),
        lambda obj: obj.product.organisation,
    ),
    "ticket-message": (
        TicketMessage,
        frozenset({"attachment"}),
        lambda obj: obj.ticket.organisation,
    ),
}


class DocumentDownloadView(LoginRequiredMixin, View):
    """Stream a stored file to its organisation's members or to a reviewer.

    Files are never linked by storage path: this is the one URL that hands a
    file out, and it checks who is asking first.
    """

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        try:
            model, fields, owner = DOCUMENT_KINDS[kwargs["kind"]]
        except KeyError as exc:
            raise Http404 from exc
        field = kwargs["field"]
        if field not in fields:
            raise Http404
        instance = get_object_or_404(model, pk=kwargs["pk"])
        organisation = owner(instance)
        allowed = (
            is_ohc_team(request.user)
            or organisation.memberships.filter(
                user=request.user,
            ).exists()
        )
        if not allowed:
            msg = _("You cannot download this file.")
            raise PermissionDenied(msg)
        value = getattr(instance, field)
        if not value:
            raise Http404
        name = Path(value.name).name
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(
            value.open("rb"),
            as_attachment=True,
            filename=name,
            content_type=content_type,
        )


class QueryReplyView(OrganisationMixin, View):
    """Answer one open reviewer query. The query must belong to this org."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        query = get_object_or_404(
            ReviewQuery.objects.select_related("item"),
            pk=kwargs["pk"],
            item__organisation=self.organisation,
        )
        form = QueryReplyForm(request.POST)
        if form.is_valid():
            try:
                services.reply_to_query(
                    query=query,
                    user=request.user,
                    reply=form.cleaned_data["reply"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _("Your reply was sent to the reviewer."))
        else:
            messages.error(request, _("Write a reply before sending it."))
        if request.htmx:
            context = query_thread_context(
                query.item,
                can_reply=can_write(self.membership),
            )
            context["reply_form"] = form if not form.is_valid() else QueryReplyForm()
            return render(request, "abdm/partials/query_thread_swap.html", context)
        return redirect(self.next_url(request))

    def next_url(self, request: HttpRequest) -> str:
        candidate = request.POST.get("next", "")
        if candidate and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
        ):
            return candidate
        return "/"


# ── products ───────────────────────────────────────────────────────────────


def _redirect_for_request(request: HttpRequest, url: str) -> HttpResponse:
    if request.htmx:
        return HttpResponseClientRedirect(url)
    return redirect(url)


class ProductMixin(OrganisationMixin):
    """Resolve the product by sandbox id inside the signed-in organisation.

    Another organisation's id is simply not in the queryset and 404s. The id
    is remembered in the session so the sidebar keeps pointing at this product
    on pages that have none of their own (Events, Support, Settings).
    """

    nav_section = ""

    def setup(self, request: HttpRequest, *args, **kwargs) -> None:
        super().setup(request, *args, **kwargs)
        self.product = None

    def get_product(self):
        if self.product is None:
            self.product = get_object_or_404(
                products_for(self.organisation),
                sandbox_id=self.kwargs["sandbox_id"],
            )
            self.request.session[SESSION_PRODUCT_KEY] = self.product.sandbox_id
        return self.product

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "product": self.get_product(),
                "nav_section": self.nav_section,
                "can_write": can_write(self.membership),
            },
        )
        return context


class ProductFormMixin:
    """Shared plumbing for the three screens that carry the product form."""

    form_class = ProductForm
    # The page includes this fragment; htmx swaps the same file back in.
    partial_template_name = "abdm/partials/product_form.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["tracks"] = tracks.TRACKS
        context["form_tracks"] = tracks.FORM_TRACKS
        return context

    def form_invalid(self, form):
        if self.request.htmx:
            return render(
                self.request,
                self.partial_template_name,
                self.get_context_data(form=form),
            )
        return super().form_invalid(form)


class ProductOnboardingView(ProductFormMixin, OrganisationMixin, FormView):
    """Onboarding step 3 — register the first product, outside the shell."""

    template_name = "abdm/onboarding_product.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.organisation.has_submitted_details:
            return redirect("organisations:onboarding")
        if products_for(self.organisation).exists():
            return redirect("dashboard")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "form_layout": "onboarding",
                "onboarding_step": 3,
                "can_write": can_write(self.membership),
            },
        )
        return context

    def form_valid(self, form):
        if not can_write(self.membership):
            msg = _("Support members cannot register products.")
            raise PermissionDenied(msg)
        product = services.register_product(
            organisation=self.organisation,
            user=self.request.user,
            data=form.product_data(),
        )
        messages.success(
            self.request,
            _("%(name)s is registered as %(sandbox_id)s and is with the review team.")
            % {"name": product.name, "sandbox_id": product.sandbox_id},
        )
        return _redirect_for_request(self.request, product.get_absolute_url())


class ProductCreateView(ProductFormMixin, OrganisationMixin, FormView):
    """Register another product from inside the shell."""

    template_name = "abdm/product_form.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.organisation.has_submitted_details:
            return redirect("organisations:onboarding")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "form_layout": "create",
                "nav_section": "product-form",
                "can_write": can_write(self.membership),
            },
        )
        return context

    def form_valid(self, form):
        if not can_write(self.membership):
            msg = _("Support members cannot register products.")
            raise PermissionDenied(msg)
        product = services.register_product(
            organisation=self.organisation,
            user=self.request.user,
            data=form.product_data(),
        )
        messages.success(
            self.request,
            _("%(name)s is registered as %(sandbox_id)s and is with the review team.")
            % {"name": product.name, "sandbox_id": product.sandbox_id},
        )
        return _redirect_for_request(self.request, product.get_absolute_url())


class ProductEditView(ProductFormMixin, ProductMixin, FormView):
    """Edit a product. Approved and under-review milestones stay locked."""

    template_name = "abdm/product_form.html"
    nav_section = "edit"

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_product()
        return kwargs

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["form_layout"] = "edit"
        return context

    def form_valid(self, form):
        if not can_write(self.membership):
            msg = _("Support members cannot change products.")
            raise PermissionDenied(msg)
        try:
            product = services.update_product(
                product=self.get_product(),
                user=self.request.user,
                data=form.product_data(),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Product details saved."))
        return _redirect_for_request(self.request, product.get_absolute_url())


class ProductOverviewView(ProductMixin, TemplateView):
    """The product's home: tracks, activity, organisation, credentials, events."""

    template_name = "abdm/product_overview.html"
    nav_section = "overview"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        product = self.get_product()
        registration_item = product.review_items.filter(
            item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
        ).first()
        context.update(
            {
                "track_summaries": track_summaries(product),
                "activity": activity_for_product(product),
                "credential": product.active_credential,
                "registration_item": registration_item,
                "upcoming_events": dashboard_events(),
                "registered_ids": registered_event_ids(self.request.user),
            },
        )
        if registration_item is not None:
            context.update(
                query_thread_context(
                    registration_item,
                    can_reply=can_write(self.membership),
                ),
            )
        return context


def credentials_context(product, membership, *, urls_form=None) -> dict:
    """What the credentials page and its swapped cards need."""
    credential = getattr(product, "credential", None)
    active = credential if credential and credential.is_active else None
    if urls_form is None:
        urls_form = CallbackUrlsForm(
            initial={
                "callback_url": active.callback_url if active else "",
                "bridge_url": active.bridge_url if active else "",
            },
        )
    return {
        "credential": active,
        "revoked_credential": credential if credential and not active else None,
        "urls_form": urls_form,
        "can_write": can_write(membership),
        "gateway_url": settings.ABDM_SANDBOX_GATEWAY_URL,
    }


class CredentialsView(ProductMixin, TemplateView):
    """Sandbox credentials for one product (design doc 5.5)."""

    template_name = "abdm/credentials.html"
    nav_section = "credentials"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(credentials_context(self.get_product(), self.membership))
        return context


class CredentialActionView(ProductMixin, View):
    """Base for the POST-only credential actions.

    htmx gets the card that changed; everyone else gets POST → redirect → flash
    back to the credentials page, which is the path that has to keep working.
    """

    card_template = "abdm/partials/credentials_card_swap.html"

    def credentials_url(self) -> str:
        return reverse(
            "products:credentials",
            kwargs={"sandbox_id": self.get_product().sandbox_id},
        )

    def respond(self, request: HttpRequest, **extra) -> HttpResponse:
        if request.htmx:
            product = products_for(self.organisation).get(pk=self.get_product().pk)
            context = {
                "product": product,
                "organisation": self.organisation,
                **credentials_context(product, self.membership, **extra),
            }
            return render(request, self.card_template, context)
        return redirect(self.credentials_url())

    def require_write(self) -> None:
        if not can_write(self.membership):
            msg = _("Support members cannot change credentials.")
            raise PermissionDenied(msg)


class CredentialRevealView(CredentialActionView):
    """Reveal the client secret once. Audited and rate-limited in services."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        product = self.get_product()
        try:
            secret = services.reveal_secret(product=product, user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return self.respond(request)
        if request.htmx:
            return render(
                request,
                "abdm/partials/secret_reveal.html",
                {"product": product, "secret": secret, "revealed": True},
            )
        # No script: the whole page comes back with the secret shown, once.
        context = {
            "product": product,
            "organisation": self.organisation,
            "membership": self.membership,
            "nav_section": "credentials",
            "revealed_secret": secret,
            **credentials_context(product, self.membership),
        }
        return render(request, "abdm/credentials.html", context)


class CredentialRotateView(CredentialActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        try:
            services.rotate_credentials(product=self.get_product(), user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _("The client secret was rotated. Reveal it to copy the new value."),
            )
        return self.respond(request)


class CredentialRevokeView(CredentialActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        try:
            services.revoke_credentials(product=self.get_product(), user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Sandbox credentials revoked."))
        return self.respond(request)


class CredentialRequestView(CredentialActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        try:
            services.request_credentials(product=self.get_product(), user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("New sandbox credentials issued."))
        return self.respond(request)


class CallbackUrlsView(CredentialActionView):
    card_template = "abdm/partials/callback_card_swap.html"

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        form = CallbackUrlsForm(request.POST)
        if not form.is_valid():
            if request.htmx:
                return self.respond(request, urls_form=form)
            messages.error(request, _("Check the URLs and try again."))
            return self.respond(request, urls_form=form)
        try:
            services.update_callback_urls(
                product=self.get_product(),
                user=request.user,
                callback_url=form.cleaned_data["callback_url"],
                bridge_url=form.cleaned_data["bridge_url"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Callback and bridge URLs saved."))
        return self.respond(request)


class CallbackTestView(CredentialActionView):
    card_template = "abdm/partials/callback_card_swap.html"

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        try:
            credential = services.check_callback(
                product=self.get_product(),
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if credential.callback_ok:
                messages.success(
                    request,
                    _("Callback reachable: HTTP %(code)s in %(ms)s ms.")
                    % {
                        "code": credential.callback_status_code,
                        "ms": credential.callback_latency_ms,
                    },
                )
            else:
                messages.warning(
                    request,
                    _("Callback unreachable: %(error)s")
                    % {
                        "error": credential.callback_error
                        or credential.callback_status_code,
                    },
                )
        return self.respond(request)


def milestone_detail_context(product, entry, membership) -> dict:
    """Everything abdm/partials/milestone_detail.html needs for one tile."""
    record = entry["record"]
    milestone = entry["milestone"]
    context = {
        "product": product,
        "milestone": milestone,
        "record": record,
        "can_write": can_write(membership),
        "prerequisite": None,
        "exit_form": None,
        "review_item": None,
    }
    if record is None:
        return context
    if record.is_locked:
        context["prerequisite"] = tracks.previous_milestone(
            tracks.get_track(milestone.track_code),
            milestone,
        )
    if record.is_editable:
        context["exit_form"] = ExitRequestForm(instance=record)
    item = record.review_item
    if item is not None:
        context["review_item"] = item
        context.update(query_thread_context(item, can_reply=can_write(membership)))
    return context


class TrackView(ProductMixin, TemplateView):
    """One track: milestone tiles and the selected milestone's exit request."""

    template_name = "abdm/track.html"
    nav_section = "track"

    def get_track(self):
        try:
            return tracks.get_track(self.kwargs["track"])
        except ValidationError as exc:
            raise Http404 from exc

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        product = self.get_product()
        track = self.get_track()
        entries = product.records_for_track(track)
        applied = product.has_track(track.code)
        wanted = self.request.GET.get("milestone", "")
        # ?milestone= wins; otherwise the first applied milestone that is not
        # approved yet (the one being worked), else the first applied one.
        selected = (
            next(
                (entry for entry in entries if entry["milestone"].code == wanted),
                None,
            )
            or next(
                (
                    entry
                    for entry in entries
                    if entry["record"] and not entry["record"].is_approved
                ),
                None,
            )
            or next((entry for entry in entries if entry["record"]), None)
        )
        if selected is None and entries:
            selected = entries[0]
        approved, applied_count = product.approved_count(track)
        context.update(
            {
                "track": track,
                "nav_track": track.code,
                "entries": entries,
                "track_applied": applied,
                "selected": selected,
                "approved_count": approved,
                "applied_count": applied_count,
            },
        )
        if selected is not None:
            context.update(milestone_detail_context(product, selected, self.membership))
        return context


class MilestoneActionMixin(ProductMixin):
    """Resolve one compliance record of this product by track and milestone."""

    def get_record(self):
        product = self.get_product()
        return get_object_or_404(
            product.compliance_records,
            track_code=self.kwargs["track"],
            milestone_code=self.kwargs["milestone"],
        )

    def track_url(self, record) -> str:
        base = reverse(
            "products:track",
            kwargs={
                "sandbox_id": self.get_product().sandbox_id,
                "track": record.track_code,
            },
        )
        return f"{base}?milestone={record.milestone_code}"

    def respond(self, request: HttpRequest, record, *, form=None) -> HttpResponse:
        record.refresh_from_db()
        if request.htmx:
            entry = {"milestone": record.milestone, "record": record}
            context = milestone_detail_context(
                self.get_product(),
                entry,
                self.membership,
            )
            if form is not None:
                context["exit_form"] = form
            return render(request, "abdm/partials/milestone_detail_swap.html", context)
        return redirect(self.track_url(record))

    def require_write(self) -> None:
        if not can_write(self.membership):
            msg = _("Support members cannot change exit requests.")
            raise PermissionDenied(msg)


class MilestoneSaveView(MilestoneActionMixin, View):
    """Save the draft; with action=request, then request exit."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        record = self.get_record()
        form = ExitRequestForm(request.POST, request.FILES, instance=record)
        if not form.is_valid():
            if not request.htmx:
                messages.error(request, _("Check the form and try again."))
            return self.respond(request, record, form=form)
        try:
            record = services.save_exit_draft(
                record=record,
                user=request.user,
                form=form,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return self.respond(request, record)
        if request.POST.get("action") == "request":
            try:
                services.request_exit(record=record, user=request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(
                    request,
                    _("Exit request for %(label)s sent to the review team.")
                    % {"label": record.label},
                )
        else:
            messages.success(request, _("Draft saved."))
        return self.respond(request, record)


class MilestoneWithdrawView(MilestoneActionMixin, View):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.require_write()
        record = self.get_record()
        try:
            services.withdraw_exit(record=record, user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _("Exit request withdrawn. The form is open for changes again."),
            )
        return self.respond(request, record)
