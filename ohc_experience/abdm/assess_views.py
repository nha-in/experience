"""Reviewer screens: the dashboard, the queue and the review detail.

Every view inherits OhcConsoleMixin — the gate that keeps integrators out of
every other organisation's data — and renders in the console shell. Writes go
through services; views collect input and draw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView
from django.views.generic import TemplateView

from ohc_experience.ohc.views import OhcConsoleMixin

from . import services
from .forms import ApproveForm
from .forms import AssignForm
from .forms import QueueFilterForm
from .forms import RaiseQueryForm
from .forms import SendBackForm
from .models import ReviewItem
from .selectors import dashboard_stats
from .selectors import integrator_context
from .selectors import queue_chip_counts
from .selectors import queue_items
from .selectors import submitted_form_sections

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

QUEUE_PAGE_SIZE = 25


class DashboardView(OhcConsoleMixin, TemplateView):
    """The reviewer dashboard (design doc 5.9)."""

    template_name = "abdm/assess/dashboard.html"
    nav_section = "dashboard"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(dashboard_stats(user=self.request.user))
        return context


class QueueView(OhcConsoleMixin, ListView):
    """The review queue (design doc 5.10): chips, a table, oldest first."""

    template_name = "abdm/assess/queue.html"
    partial_template_name = "abdm/assess/partials/queue_results.html"
    context_object_name = "items"
    paginate_by = QUEUE_PAGE_SIZE
    nav_section = "review-queue"

    @cached_property
    def filter_form(self) -> QueueFilterForm:
        data = self.request.GET.copy()
        data.setdefault("scope", "open")
        return QueueFilterForm(data)

    def get_queryset(self):
        return queue_items(
            chip=self.filter_form.chosen("chip"),
            scope=self.filter_form.chosen("scope") or "open",
            user=self.request.user,
        )

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        chip = self.filter_form.chosen("chip")
        scope = self.filter_form.chosen("scope") or "open"
        params = self.filter_form.data.copy()
        params.pop("page", None)
        context.update(
            {
                "chips": QueueFilterForm.CHIPS,
                "chip_counts": queue_chip_counts(scope=scope, user=self.request.user),
                "scopes": QueueFilterForm.SCOPES,
                "chip": chip,
                "scope": scope,
                "filter_query": params.urlencode(),
            },
        )
        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        # A boosted nav click is also an htmx request, and it wants the whole
        # page; only the chips ask for the results on their own.
        if request.htmx and not request.htmx.boosted:
            response.template_name = self.partial_template_name
        return response


def get_item(reference: str) -> ReviewItem:
    return get_object_or_404(ReviewItem.objects.with_related(), reference=reference)


def review_context(item: ReviewItem, request: HttpRequest, **forms) -> dict:
    """Everything the review detail and its swapped workspace need."""
    approve_form = forms.get("approve_form") or ApproveForm(
        initial={
            "approved_on": timezone.localdate(),
            "approved_by": request.user.display_name,
        },
    )
    query_form = forms.get("query_form") or RaiseQueryForm(
        initial={
            "field_key": request.GET.get("field", ""),
            "field_label": request.GET.get("label", ""),
        },
    )
    queries = list(item.queries.select_related("raised_by", "replied_by"))
    open_keys = {query.field_key for query in queries if query.is_open}
    sections = submitted_form_sections(item)
    for section in sections:
        for row in section["rows"]:
            row["query_open"] = row["key"] in open_keys
    return {
        "nav_section": "review-queue",
        "item": item,
        "sections": sections,
        "queries": queries,
        "open_query_count": len(open_keys),
        "history": list(item.history.select_related("actor")),
        "approve_form": approve_form,
        "send_back_form": forms.get("send_back_form") or SendBackForm(),
        "query_form": query_form,
        "assign_form": AssignForm(initial={"assignee": item.assignee_id}),
        "can_assign": request.user.is_superuser,
        "decision_tab": request.GET.get("tab")
        or forms.get("decision_tab")
        or "approve",
        **integrator_context(item),
    }


class ReviewDetailView(OhcConsoleMixin, View):
    """One item: the submitted form, the integrator's context, the decision."""

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        return render(
            request,
            "abdm/assess/review_detail.html",
            review_context(item, request),
        )


class ReviewActionView(OhcConsoleMixin, View):
    """Base for the POST actions on a review item.

    htmx gets the workspace fragment back (the decision panel, the queries
    and the history move together); everyone else gets POST → redirect →
    flash, which is the path that has to keep working.
    """

    def swap(self, request: HttpRequest, item: ReviewItem, **forms) -> HttpResponse:
        item = get_item(item.reference)
        if request.htmx:
            return render(
                request,
                "abdm/assess/partials/review_workspace_swap.html",
                review_context(item, request, **forms),
            )
        return redirect(item.get_absolute_url())

    def reshow(self, request: HttpRequest, item: ReviewItem, **forms) -> HttpResponse:
        """A rejected submission: 200 with the errors, never a redirect."""
        item = get_item(item.reference)
        context = review_context(item, request, **forms)
        if request.htmx:
            return render(
                request,
                "abdm/assess/partials/review_workspace_swap.html",
                context,
            )
        return render(request, "abdm/assess/review_detail.html", context)


class ReviewStartView(ReviewActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        try:
            services.start_review(item=item, user=request.user)
        except ValidationError as exc:
            messages.info(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Review started."))
        return self.swap(request, item)


class ReviewAssignView(ReviewActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        form = AssignForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Pick a reviewer from the list."))
            return self.swap(request, item)
        try:
            services.assign_reviewer(
                item=item,
                actor=request.user,
                assignee=form.cleaned_data["assignee"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Assignment saved."))
        return self.swap(request, item)


class ReviewQueryView(ReviewActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        form = RaiseQueryForm(request.POST)
        if not form.is_valid():
            return self.reshow(request, item, query_form=form, decision_tab="query")
        try:
            services.raise_query(
                item=item,
                user=request.user,
                field_key=form.cleaned_data["field_key"],
                field_label=form.cleaned_data["field_label"],
                question=form.cleaned_data["question"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Query sent to the integrator."))
        return self.swap(request, item)


class ReviewApproveView(ReviewActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        form = ApproveForm(request.POST)
        if not form.is_valid():
            return self.reshow(request, item, approve_form=form, decision_tab="approve")
        try:
            services.approve(
                item=item,
                user=request.user,
                approved_on=form.cleaned_data["approved_on"],
                note=form.cleaned_data["note"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _("%(title)s approved for %(subject)s.")
                % {"title": item.title, "subject": item.subject_label},
            )
        return self.swap(request, item)


class ReviewSendBackView(ReviewActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        form = SendBackForm(request.POST)
        if not form.is_valid():
            return self.reshow(
                request,
                item,
                send_back_form=form,
                decision_tab="send_back",
            )
        try:
            services.send_back(
                item=item,
                user=request.user,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Sent back to the integrator."))
        return self.swap(request, item)


class QueryResolveView(ReviewActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        item = get_item(kwargs["reference"])
        query = get_object_or_404(item.queries, pk=kwargs["pk"])
        services.resolve_query(query=query, user=request.user)
        messages.success(request, _("Query marked resolved."))
        return self.swap(request, item)
