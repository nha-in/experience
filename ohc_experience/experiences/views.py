from __future__ import annotations

from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.db.models import Q
from django.http import FileResponse
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView
from django.views.generic import ListView
from django.views.generic import TemplateView
from django.views.generic import UpdateView
from django_htmx.http import HttpResponseClientRedirect

from ohc_experience.organisations.views import OrganisationMixin
from ohc_experience.users.permissions import OhcTeamRequiredMixin

from . import permission_keys
from .forms import ApplicationAccessForm
from .forms import ApplicationFilterForm
from .forms import ProductForm
from .forms import QueryReplyForm
from .forms import StartApplicationForm
from .models import ApplicationInstance
from .models import FormAttachment
from .models import FormRecord
from .models import Product
from .models import QueryStatus
from .permissions import get_effective_access
from .registry import registry
from .selectors import PENDING_QUERY_STATUSES
from .selectors import applications_for_user
from .selectors import filter_applications
from .services import application_context
from .services import assignable_roles
from .services import create_application
from .services import grant_application_access
from .services import perform_application_action
from .services import perform_form_action
from .services import post_query_reply
from .services import remove_application_access
from .services import resolve_query
from .services import save_form_submission


def _status_for(application):
    return registry.get(application.application_type).get_status(application.status)


def _redirect_for_request(request, url: str):
    if request.htmx:
        return HttpResponseClientRedirect(url)
    return redirect(url)


def _filter_querystring(filters: dict[str, object]) -> str:
    values = {
        key: value.pk if isinstance(value, Product) else value
        for key, value in filters.items()
    }
    return urlencode(values)


def _decorate_applications(applications) -> None:
    for application in applications:
        application.definition = registry.get(application.application_type)
        application.status_definition = application.definition.get_status(
            application.status,
        )


class ProductObjectMixin:
    def dispatch(self, request, *args, **kwargs):
        self.product = get_object_or_404(
            Product.objects.for_organisation(self.organisation).select_related(
                "organisation",
                "created_by",
            ),
            slug=kwargs["slug"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.product

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "product": self.product,
                "nav_section": "products",
                "can_edit_product": self.product.can_edit(self.request.user),
            },
        )
        return context


class ProductListView(OrganisationMixin, ListView):
    template_name = "experiences/product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        return (
            Product.objects.for_organisation(self.organisation)
            .select_related("created_by")
            .annotate(
                application_count=Count("applications", distinct=True),
                active_application_count=Count(
                    "applications",
                    filter=~Q(applications__status__in={"approved", "rejected"}),
                    distinct=True,
                ),
            )
            .order_by("name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "nav_section": "products",
                "product_count": self.get_queryset().count(),
            },
        )
        return context


class ProductCreateView(OrganisationMixin, CreateView):
    template_name = "experiences/product_form.html"
    form_class = ProductForm
    model = Product

    def form_valid(self, form):
        product = form.save(commit=False)
        product.organisation = self.organisation
        product.created_by = self.request.user
        product.save()
        messages.success(self.request, _("Product created."))
        return _redirect_for_request(self.request, product.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "nav_section": "products",
                "page_title": _("Add product"),
                "submit_label": _("Create product"),
            },
        )
        return context


class ProductDetailView(
    OrganisationMixin,
    ProductObjectMixin,
    TemplateView,
):
    template_name = "experiences/product_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        applications = list(
            applications_for_user(self.request.user)
            .filter(product=self.product)
            .order_by("-updated_at", "-pk"),
        )
        _decorate_applications(applications)
        form_records = list(
            FormRecord.objects.with_workspace_data()
            .filter(application_uses__application__product=self.product)
            .annotate(
                application_count=Count(
                    "application_uses__application",
                    distinct=True,
                ),
            )
            .distinct()
            .order_by("name", "created_at"),
        )
        for form_record in form_records:
            form_record.latest_submission = form_record.current_submission
        outcomes = list(
            self.product.outcomes.select_related(
                "source_application",
                "issued_by",
            ),
        )
        for outcome in outcomes:
            outcome.rows = _product_outcome_rows(outcome)
        context.update(
            {
                "applications": applications,
                "available_definitions": registry.all(),
                "form_records": form_records,
                "product_outcomes": outcomes,
            },
        )
        return context


class ProductUpdateView(
    OrganisationMixin,
    ProductObjectMixin,
    UpdateView,
):
    template_name = "experiences/product_form.html"
    form_class = ProductForm
    model = Product

    def get(self, request, *args, **kwargs):
        if not self.product.can_edit(request.user):
            raise PermissionDenied(_("You cannot edit this product."))
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.product.can_edit(request.user):
            raise PermissionDenied(_("You cannot edit this product."))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        product = form.save()
        messages.success(self.request, _("Product updated."))
        return _redirect_for_request(self.request, product.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "page_title": _("Edit product"),
                "submit_label": _("Save product"),
            },
        )
        return context


def _choice_map(choices) -> dict[str, str]:
    result = {}
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            result.update(_choice_map(label))
        else:
            result[str(value)] = str(label)
    return result


def _display_historical_value(field, schema, value) -> str:
    if value in (None, "", []):
        return str(_("Not provided"))
    choices = {
        str(item.get("value")): str(item.get("label"))
        for item in schema.get("choices", [])
        if isinstance(item, dict)
    }
    if not choices and field is not None and getattr(field, "choices", None):
        choices = _choice_map(field.choices)
    if schema.get("type") == "BooleanField" or isinstance(
        field,
        forms.BooleanField,
    ):
        return str(_("Yes")) if value else str(_("No"))
    if isinstance(value, list):
        return ", ".join(choices.get(str(item), str(item)) for item in value)
    if choices:
        return choices.get(str(value), str(value))
    if isinstance(value, dict):
        return str(value.get("name") or value)
    return str(value)


def _outcome_rows(outcome: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for key, value in outcome.items():
        if key in {"decision_note", "details"}:
            continue
        display_value = (
            ", ".join(str(item) for item in value)
            if isinstance(value, list)
            else str(value)
        )
        rows.append(
            {
                "label": key.replace("_", " ").title(),
                "value": display_value,
            },
        )
    return rows


def _product_outcome_rows(outcome) -> list[dict[str, Any]]:
    schema_by_key = {
        str(item.get("key")): item
        for item in outcome.field_schema
        if isinstance(item, dict) and item.get("key")
    }
    keys = [*schema_by_key, *(key for key in outcome.data if key not in schema_by_key)]
    return [
        {
            "key": key,
            "label": schema_by_key.get(key, {}).get("label")
            or key.replace("_", " ").title(),
            "value": outcome.data.get(key, ""),
            "display_value": (
                ", ".join(str(item) for item in outcome.data.get(key, []))
                if isinstance(outcome.data.get(key), list)
                else str(outcome.data.get(key, ""))
            ),
            "secret": bool(schema_by_key.get(key, {}).get("secret")),
        }
        for key in keys
    ]


def _submission_rows(form_definition, submission) -> list[dict[str, Any]]:
    attachments = {}
    for attachment in submission.attachments.filter(is_current=True):
        attachments.setdefault(attachment.field_key, []).append(attachment)
    current_fields = form_definition.form_class.base_fields
    schema = submission.field_schema or [
        {
            "key": field_name,
            "label": str(field.label or field_name.replace("_", " ").title()),
            "type": field.__class__.__name__,
            "choices": [],
        }
        for field_name, field in current_fields.items()
    ]
    rows = []
    rendered_keys = set()
    for item in schema:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        field_name = str(item["key"])
        rendered_keys.add(field_name)
        field = current_fields.get(field_name)
        rows.append(
            {
                "label": item.get("label") or field_name.replace("_", " ").title(),
                "value": _display_historical_value(
                    field,
                    item,
                    submission.data.get(field_name),
                ),
                "attachments": attachments.get(field_name, []),
            },
        )
    remaining_keys = [
        key for key in [*submission.data, *attachments] if key not in rendered_keys
    ]
    for field_name in dict.fromkeys(remaining_keys):
        field = current_fields.get(field_name)
        rows.append(
            {
                "label": str(
                    field.label
                    if field and field.label
                    else field_name.replace("_", " ").title(),
                ),
                "value": _display_historical_value(
                    field,
                    {},
                    submission.data.get(field_name),
                ),
                "attachments": attachments.get(field_name, []),
            },
        )
    return rows


def submission_sections(definition, context) -> list[dict[str, Any]]:
    sections = []
    for form_definition in definition.forms:
        submission = context.submissions.get(form_definition.key)
        if submission is None:
            continue
        history = [
            {
                "submission": historical_submission,
                "rows": _submission_rows(form_definition, historical_submission),
            }
            for historical_submission in context.form_history(form_definition.key)
            if historical_submission.pk != submission.pk
        ]
        sections.append(
            {
                "definition": form_definition,
                "form_record": context.form_records[form_definition.key],
                "form_use": context.form_uses[form_definition.key],
                "reused": context.form_uses[form_definition.key].is_reused,
                "used_by_count": len(
                    context.form_records[form_definition.key].application_uses.all(),
                ),
                "submission": submission,
                "rows": _submission_rows(form_definition, submission),
                "history": history,
                "form_action_states": [
                    state
                    for state in form_definition.action_states(context)
                    if state.available or state.outcome_label
                ],
            },
        )
    return sections


class ApplicationObjectMixin:
    console = False

    def dispatch(self, request, *args, **kwargs):
        queryset = applications_for_user(request.user)
        if not self.console:
            queryset = queryset.filter(product__organisation=self.organisation)
        self.application = get_object_or_404(
            queryset,
            reference=kwargs["reference"],
        )
        self.definition = registry.get(self.application.application_type)
        self.experience_context = application_context(
            self.application,
            request.user,
        )
        if not self.experience_context.has_permission(
            permission_keys.VIEW_APPLICATION,
        ):
            raise PermissionDenied(_("You cannot view this application."))
        return super().dispatch(request, *args, **kwargs)

    def detail_url(self) -> str:
        name = "ohc:application-detail" if self.console else "experiences:detail"
        return reverse(name, kwargs={"reference": self.application.reference})

    def common_context(self) -> dict[str, Any]:
        access = get_effective_access(self.application, self.request.user)
        permission_rows = [
            {
                "definition": permission,
                "allowed": permission.key in access.permissions,
            }
            for permission in self.definition.permissions
        ]
        grants = list(self.application.access_grants.select_related("user"))
        for grant in grants:
            grant.role_definition = self.definition.get_role(grant.role_key)
        dependencies = list(
            self.application.dependencies.with_workspace_data().order_by(
                "created_at",
                "pk",
            ),
        )
        _decorate_applications(dependencies)
        dependents = list(
            self.application.dependent_applications.with_workspace_data().order_by(
                "created_at",
                "pk",
            ),
        )
        _decorate_applications(dependents)
        unmet_dependencies = self.definition.unmet_dependencies(
            self.experience_context,
        )
        return {
            "application": self.application,
            "definition": self.definition,
            "experience_context": self.experience_context,
            "status_definition": self.definition.get_status(self.application.status),
            "effective_access": access,
            "permission_rows": permission_rows,
            "access_grants": grants,
            "dependencies": dependencies,
            "dependents": dependents,
            "unmet_dependencies": unmet_dependencies,
            "console": self.console,
            "layout_template": "layouts/ohc.html"
            if self.console
            else "layouts/app.html",
            "nav_section": "applications",
            "detail_url": self.detail_url(),
        }


class HtmxApplicationListMixin:
    partial_template_name: str

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        if request.htmx and not request.htmx.boosted:
            return render(request, self.partial_template_name, response.context_data)
        return response


class VendorApplicationListView(
    OrganisationMixin,
    HtmxApplicationListMixin,
    ListView,
):
    template_name = "experiences/application_list.html"
    partial_template_name = "experiences/partials/application_results.html"
    context_object_name = "applications"
    paginate_by = 20

    def get_queryset(self):
        queryset = applications_for_user(self.request.user).filter(
            product__organisation=self.organisation,
        )
        self.filter_form = ApplicationFilterForm(
            self.request.GET,
            product_queryset=Product.objects.for_organisation(self.organisation),
        )
        self.filters = self.filter_form.selected()
        return filter_applications(queryset, self.filters).order_by(
            "-updated_at",
            "-pk",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        applications = list(context["applications"])
        _decorate_applications(applications)
        base = ApplicationInstance.objects.visible_to(self.request.user).filter(
            product__organisation=self.organisation,
        )
        context.update(
            {
                "applications": applications,
                "filter_form": self.filter_form,
                "filter_querystring": _filter_querystring(self.filters),
                "nav_section": "applications",
                "product_count": Product.objects.for_organisation(
                    self.organisation,
                ).count(),
                "total_count": base.count(),
                "in_review_count": base.filter(
                    status__in=["submitted", "under_review", "revision_submitted"],
                ).count(),
                "query_count": base.filter(
                    query_threads__status__in=PENDING_QUERY_STATUSES,
                )
                .distinct()
                .count(),
                "approved_count": base.filter(status="approved").count(),
            },
        )
        return context


class StartApplicationView(
    OrganisationMixin,
    ProductObjectMixin,
    TemplateView,
):
    template_name = "experiences/application_start.html"

    def dispatch(self, request, *args, **kwargs):
        try:
            self.definition = registry.get(kwargs["application_type"])
        except ImproperlyConfigured as exc:
            raise Http404 from exc
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get("form") or StartApplicationForm(
            product=self.product,
            user=self.request.user,
        )
        context.update(
            {
                "definition": self.definition,
                "form": form,
                "nav_section": "products",
            },
        )
        return context

    def post(self, request, *args, **kwargs):
        form = StartApplicationForm(
            request.POST,
            product=self.product,
            user=request.user,
        )
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        application = create_application(
            application_type=self.definition.key,
            product=self.product,
            user=request.user,
            dependencies=form.cleaned_data["dependencies"],
        )
        messages.success(request, _("Application workspace created."))
        return _redirect_for_request(
            request,
            reverse("experiences:detail", kwargs={"reference": application.reference}),
        )


class ApplicationDetailContextMixin(ApplicationObjectMixin):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        action_states = self.definition.action_states(self.experience_context)
        form_states = self.definition.form_states(self.experience_context)
        query_threads = list(
            self.application.query_threads.select_related(
                "submission",
                "opened_by",
            ),
        )
        for thread in query_threads:
            thread.needs_attention = (
                self.console and thread.status == QueryStatus.AWAITING_REVIEWER
            ) or (not self.console and thread.status == QueryStatus.AWAITING_APPLICANT)
        open_query_count = sum(
            thread.status in PENDING_QUERY_STATUSES for thread in query_threads
        )
        context.update(self.common_context())
        context.update(
            {
                "form_states": [item for item in form_states if item.visible],
                "available_actions": [item for item in action_states if item.available],
                "blocked_actions": [
                    item
                    for item in action_states
                    if not item.available
                    and self.experience_context.has_permission(
                        item.definition.permission,
                    )
                    and (
                        not item.definition.allowed_statuses
                        or self.application.status in item.definition.allowed_statuses
                    )
                ],
                "query_threads": query_threads,
                "open_query_count": open_query_count,
                "recent_events": self.application.events.select_related("actor")[:12],
                "outcome_rows": _outcome_rows(self.application.outcome),
                "submission_sections": submission_sections(
                    self.definition,
                    self.experience_context,
                ),
                "can_manage_access": bool(
                    assignable_roles(self.application, self.request.user),
                ),
            },
        )
        return context


class VendorApplicationDetailView(
    OrganisationMixin,
    ApplicationDetailContextMixin,
    TemplateView,
):
    template_name = "experiences/application_detail.html"


class AdminApplicationDetailView(
    OhcTeamRequiredMixin,
    ApplicationDetailContextMixin,
    TemplateView,
):
    template_name = "experiences/admin/application_detail.html"
    console = True


class AdminApplicationListView(
    OhcTeamRequiredMixin,
    HtmxApplicationListMixin,
    ListView,
):
    template_name = "experiences/admin/application_list.html"
    partial_template_name = "experiences/admin/partials/application_results.html"
    context_object_name = "applications"
    paginate_by = 30

    def get_queryset(self):
        base = ApplicationInstance.objects.visible_to(self.request.user)
        self.filter_form = ApplicationFilterForm(
            self.request.GET,
            product_queryset=Product.objects.filter(
                applications__in=base,
            )
            .distinct()
            .order_by("name"),
        )
        self.filters = self.filter_form.selected()
        queryset = filter_applications(
            base.with_workspace_data(),
            self.filters,
        )
        return queryset.order_by("-updated_at", "-pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        applications = list(context["applications"])
        _decorate_applications(applications)
        base = ApplicationInstance.objects.visible_to(self.request.user)
        context.update(
            {
                "applications": applications,
                "filter_form": self.filter_form,
                "filter_querystring": _filter_querystring(self.filters),
                "nav_section": "applications",
                "total_count": base.count(),
                "waiting_count": base.filter(
                    status__in=["submitted", "revision_submitted"],
                ).count(),
                "review_count": base.filter(status="under_review").count(),
                "query_count": base.filter(
                    query_threads__status__in=PENDING_QUERY_STATUSES,
                )
                .distinct()
                .count(),
                "decided_count": base.filter(
                    status__in=["approved", "rejected"],
                ).count(),
            },
        )
        return context


class FormWorkspaceMixin(ApplicationObjectMixin):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.form_key = kwargs.get("form_key", "")

    def get_form_definition(self, *, require_edit=False):
        definition = self.definition.get_form(self.form_key)
        if definition is None:
            raise Http404
        submission = self.experience_context.submissions.get(self.form_key)
        if require_edit:
            available, reason = definition.availability(self.experience_context)
            if not available:
                raise PermissionDenied(reason)
        elif submission is None and not definition.is_visible(
            self.experience_context,
        ):
            raise PermissionDenied(_("Complete the preceding forms first."))
        return definition

    def get_submission_mode(self, form_definition=None):
        form_definition = form_definition or self.get_form_definition()
        submission = self.experience_context.submissions.get(self.form_key)
        if submission is None:
            return "create"
        if not form_definition.repeatable:
            return "edit"
        requested_mode = self.request.POST.get(
            "submission_mode",
            self.request.GET.get("mode", "renew"),
        )
        if requested_mode not in {"edit", "renew"}:
            raise Http404
        if requested_mode == "edit" and not form_definition.allow_updates:
            raise PermissionDenied(
                _("This submission is locked after it is completed."),
            )
        return requested_mode

    def render_form(self, form, *, partial=False, status=HTTPStatus.OK):
        form_definition = self.get_form_definition()
        submission_mode = self.get_submission_mode(form_definition)
        submission = self.experience_context.submissions.get(self.form_key)
        form_record = self.experience_context.form_records[self.form_key]
        form_use = self.experience_context.form_uses[self.form_key]
        history = self.experience_context.form_history(self.form_key)
        can_submit, read_only_reason = form_definition.availability(
            self.experience_context,
        )
        context = {
            **self.common_context(),
            "form": form,
            "form_definition": form_definition,
            "form_record": form_record,
            "form_use": form_use,
            "form_used_by_count": len(form_record.application_uses.all()),
            "submission": submission,
            "submission_mode": submission_mode,
            "can_submit": can_submit,
            "read_only_reason": read_only_reason,
            "current_submission_rows": (
                _submission_rows(form_definition, submission) if submission else []
            ),
            "submission_history": history,
            "submission_history_sections": [
                {
                    "submission": historical,
                    "rows": _submission_rows(form_definition, historical),
                }
                for historical in history
                if submission is None or historical.pk != submission.pk
            ],
        }
        return render(
            self.request,
            (
                "experiences/partials/form_submission_form.html"
                if partial
                else "experiences/form_workspace.html"
            ),
            context,
            status=status,
        )

    def get(self, request, *args, **kwargs):
        definition = self.get_form_definition()
        submission_mode = self.get_submission_mode(definition)
        can_submit, _reason = definition.availability(self.experience_context)
        form = (
            definition.build_form(
                context=self.experience_context,
                submission_mode=submission_mode,
            )
            if can_submit
            else None
        )
        return self.render_form(form)

    def post(self, request, *args, **kwargs):
        definition = self.get_form_definition(require_edit=True)
        submission_mode = self.get_submission_mode(definition)
        form = definition.build_form(
            context=self.experience_context,
            data=request.POST,
            files=request.FILES,
            submission_mode=submission_mode,
        )
        if not form.is_valid():
            return self.render_form(form, partial=bool(request.htmx))
        save_form_submission(
            application=self.application,
            form_key=self.form_key,
            form=form,
            user=request.user,
            submission_mode=submission_mode,
        )
        messages.success(
            request,
            _("%(form)s saved.") % {"form": definition.name},
        )
        return _redirect_for_request(request, self.detail_url())


class VendorFormWorkspaceView(
    OrganisationMixin,
    FormWorkspaceMixin,
    View,
):
    pass


class ActionWorkspaceMixin(ApplicationObjectMixin):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.action_key = kwargs.get("action_key", "")

    def get_action(self):
        action = self.definition.get_action(self.action_key)
        if action is None:
            raise Http404
        available, reason = action.availability(self.experience_context)
        if not available:
            raise PermissionDenied(reason)
        return action

    def render_action(self, form, *, partial=False):
        action = self.get_action()
        return render(
            self.request,
            (
                "experiences/partials/action_form.html"
                if partial
                else "experiences/action_workspace.html"
            ),
            {
                **self.common_context(),
                "action_definition": action,
                "form": form,
            },
        )

    def get(self, request, *args, **kwargs):
        action = self.get_action()
        return self.render_action(
            action.build_form(context=self.experience_context),
        )

    def post(self, request, *args, **kwargs):
        action = self.get_action()
        form = action.build_form(context=self.experience_context, data=request.POST)
        if form is not None and not form.is_valid():
            return self.render_action(form, partial=bool(request.htmx))
        try:
            result, _query = perform_application_action(
                application=self.application,
                action_key=action.key,
                user=request.user,
                cleaned_data=form.cleaned_data if form else {},
            )
        except ValidationError as exc:
            if form is None:
                form = forms.Form()
            form.add_error(None, exc)
            return self.render_action(form, partial=bool(request.htmx))
        messages.success(request, result.message)
        return _redirect_for_request(request, self.detail_url())


class VendorActionWorkspaceView(
    OrganisationMixin,
    ActionWorkspaceMixin,
    View,
):
    pass


class AdminActionWorkspaceView(
    OhcTeamRequiredMixin,
    ActionWorkspaceMixin,
    View,
):
    console = True


class FormActionWorkspaceMixin(ApplicationObjectMixin):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.form_key = kwargs.get("form_key", "")
        self.form_action_key = kwargs.get("form_action_key", "")

    def get_form_action(self):
        form_definition = self.definition.get_form(self.form_key)
        if form_definition is None:
            raise Http404
        action = form_definition.get_action(self.form_action_key)
        if action is None:
            raise Http404
        submission = self.experience_context.submissions.get(self.form_key)
        available, reason = action.availability(
            self.experience_context,
            submission,
        )
        if not available:
            raise PermissionDenied(reason)
        return form_definition, action, submission

    def render_form_action(self, form, *, partial=False):
        form_definition, action, submission = self.get_form_action()
        return render(
            self.request,
            (
                "experiences/partials/action_form.html"
                if partial
                else "experiences/form_action_workspace.html"
            ),
            {
                **self.common_context(),
                "form_definition": form_definition,
                "action_definition": action,
                "submission": submission,
                "form": form,
            },
        )

    def get(self, request, *args, **kwargs):
        _form_definition, action, submission = self.get_form_action()
        return self.render_form_action(
            action.build_form(
                context=self.experience_context,
                submission=submission,
            ),
        )

    def post(self, request, *args, **kwargs):
        _form_definition, action, submission = self.get_form_action()
        form = action.build_form(
            context=self.experience_context,
            submission=submission,
            data=request.POST,
        )
        if form is not None and not form.is_valid():
            return self.render_form_action(form, partial=bool(request.htmx))
        try:
            result = perform_form_action(
                application=self.application,
                form_key=self.form_key,
                action_key=action.key,
                user=request.user,
                cleaned_data=form.cleaned_data if form else {},
            )
        except ValidationError as exc:
            if form is None:
                form = forms.Form()
            form.add_error(None, exc)
            return self.render_form_action(form, partial=bool(request.htmx))
        messages.success(request, result.message)
        return _redirect_for_request(request, self.detail_url())


class VendorFormActionWorkspaceView(
    OrganisationMixin,
    FormActionWorkspaceMixin,
    View,
):
    pass


class AdminFormActionWorkspaceView(
    OhcTeamRequiredMixin,
    FormActionWorkspaceMixin,
    View,
):
    console = True


class QueryWorkspaceMixin(ApplicationObjectMixin):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.query_pk = kwargs.get("query_pk")

    def get_thread(self):
        if not self.experience_context.has_permission(permission_keys.VIEW_QUERIES):
            raise PermissionDenied(_("You cannot view application queries."))
        return get_object_or_404(
            self.application.query_threads.select_related(
                "opened_by",
                "assigned_to",
                "submission",
            ),
            pk=self.query_pk,
        )

    def render_thread(self, reply_form, *, partial=False):
        thread = self.get_thread()
        thread_messages = thread.messages.select_related("author")
        if not self.console:
            thread_messages = thread_messages.filter(is_internal=False)
        context = {
            **self.common_context(),
            "thread": thread,
            "thread_messages": thread_messages,
            "reply_form": reply_form,
            "can_reply": thread.status != QueryStatus.RESOLVED
            and (
                self.experience_context.has_permission(permission_keys.RESPOND_QUERIES)
                or self.experience_context.has_permission(permission_keys.RAISE_QUERY)
            ),
            "can_resolve": self.experience_context.has_permission(
                permission_keys.RESOLVE_QUERY,
            )
            and thread.status != QueryStatus.RESOLVED,
            "query_needs_attention": (
                self.console and thread.status == QueryStatus.AWAITING_REVIEWER
            )
            or (not self.console and thread.status == QueryStatus.AWAITING_APPLICANT),
        }
        return render(
            self.request,
            (
                "experiences/partials/query_workspace_swap.html"
                if partial
                else "experiences/query_thread.html"
            ),
            context,
        )

    def get(self, request, *args, **kwargs):
        return self.render_thread(QueryReplyForm())

    def post(self, request, *args, **kwargs):
        form = QueryReplyForm(request.POST)
        if not form.is_valid():
            return self.render_thread(form, partial=bool(request.htmx))
        thread = self.get_thread()
        try:
            post_query_reply(
                thread=thread,
                user=request.user,
                body=form.cleaned_data["body"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.render_thread(form, partial=bool(request.htmx))
        messages.success(request, _("Query response posted."))
        if request.htmx:
            return self.render_thread(QueryReplyForm(), partial=True)
        return redirect(request.path)


class VendorQueryWorkspaceView(
    OrganisationMixin,
    QueryWorkspaceMixin,
    View,
):
    pass


class AdminQueryWorkspaceView(
    OhcTeamRequiredMixin,
    QueryWorkspaceMixin,
    View,
):
    console = True


class AdminResolveQueryView(
    OhcTeamRequiredMixin,
    QueryWorkspaceMixin,
    View,
):
    console = True

    def post(self, request, *args, **kwargs):
        thread = get_object_or_404(
            self.application.query_threads,
            pk=kwargs["query_pk"],
        )
        resolve_query(thread=thread, user=request.user)
        messages.success(request, _("Query resolved."))
        if request.htmx:
            return self.render_thread(QueryReplyForm(), partial=True)
        return redirect(
            "ohc:application-query",
            reference=self.application.reference,
            query_pk=thread.pk,
        )


class AccessWorkspaceMixin(ApplicationObjectMixin):
    def get_form(self, data=None):
        roles = assignable_roles(self.application, self.request.user)
        if not roles:
            raise PermissionDenied(_("You cannot manage access to this application."))
        return ApplicationAccessForm(
            data=data,
            application=self.application,
            actor=self.request.user,
        )

    def render_access(self, form, *, partial=False):
        roles = assignable_roles(self.application, self.request.user)
        audiences = {role.audience for role in roles}
        actor_access = get_effective_access(self.application, self.request.user)
        is_superuser = getattr(self.request.user, "is_superuser", False)
        grants = []
        for grant in self.application.access_grants.select_related("user"):
            role = self.definition.get_role(grant.role_key)
            if role is None or role.audience not in audiences:
                continue
            grant.role_definition = role
            target_permissions = role.permissions | set(grant.direct_permissions)
            grant.can_remove = (
                grant.user_id != self.application.created_by_id
                and role in roles
                and (is_superuser or target_permissions <= actor_access.permissions)
            )
            grants.append(grant)
        context = self.common_context()
        context.update(
            {
                "form": form,
                "assignable_roles": roles,
                "access_grants": grants,
            },
        )
        return render(
            self.request,
            (
                "experiences/partials/access_workspace_swap.html"
                if partial
                else "experiences/access_workspace.html"
            ),
            context,
        )

    def get(self, request, *args, **kwargs):
        return self.render_access(self.get_form())

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_access(form, partial=bool(request.htmx))
        grant_application_access(
            application=self.application,
            actor=request.user,
            target_user=form.cleaned_data["user"],
            role_key=form.cleaned_data["role"],
            direct_permissions=form.cleaned_data["direct_permissions"],
        )
        messages.success(request, _("Application access updated."))
        if request.htmx:
            return self.render_access(self.get_form(), partial=True)
        return redirect(request.path)


class VendorAccessWorkspaceView(
    OrganisationMixin,
    AccessWorkspaceMixin,
    View,
):
    pass


class AdminAccessWorkspaceView(
    OhcTeamRequiredMixin,
    AccessWorkspaceMixin,
    View,
):
    console = True


class RemoveAccessMixin(AccessWorkspaceMixin):
    def post(self, request, *args, **kwargs):
        target_user = get_object_or_404(
            self.application.access_grants.select_related("user"),
            user_id=kwargs["user_pk"],
        ).user
        remove_application_access(
            application=self.application,
            actor=request.user,
            target_user=target_user,
        )
        messages.success(request, _("Application access removed."))
        if request.htmx:
            return self.render_access(self.get_form(), partial=True)
        name = "ohc:application-access" if self.console else "experiences:access"
        return redirect(name, reference=self.application.reference)


class VendorRemoveAccessView(
    OrganisationMixin,
    RemoveAccessMixin,
    View,
):
    pass


class AdminRemoveAccessView(
    OhcTeamRequiredMixin,
    RemoveAccessMixin,
    View,
):
    console = True


class AttachmentDownloadView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        application = get_object_or_404(
            ApplicationInstance.objects.visible_to(request.user),
            reference=kwargs["reference"],
        )
        context = application_context(application, request.user)
        if not context.has_permission(permission_keys.VIEW_APPLICATION):
            raise PermissionDenied(_("You cannot download this attachment."))
        attachment = get_object_or_404(
            FormAttachment.objects.select_related("submission", "submission__form"),
            pk=kwargs["attachment_pk"],
            submission__form__application_uses__application=application,
        )
        return FileResponse(
            attachment.file.open("rb"),
            as_attachment=True,
            filename=attachment.original_name,
            content_type=attachment.content_type or "application/octet-stream",
        )
