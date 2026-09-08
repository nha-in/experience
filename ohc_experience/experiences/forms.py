from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import ApplicationInstance
from .models import Product
from .permissions import get_effective_access
from .registry import registry
from .services import assignable_roles


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj) -> str:
        return f"{obj.display_name} ({obj.email})"


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "product_type",
            "description",
            "website",
            "current_facility_count",
            "deployment_regions",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "deployment_regions": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "description": _(
                "Describe the users, care settings, and journeys this product "
                "supports.",
            ),
            "deployment_regions": _(
                "Enter one or more states or union territories.",
            ),
        }


class DependencyChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj) -> str:
        definition = registry.get(obj.application_type)
        return f"{obj.reference} - {definition.name} ({obj.status.replace('_', ' ')})"


class StartApplicationForm(forms.Form):
    dependencies = DependencyChoiceField(
        label=_("Prerequisite applications"),
        queryset=ApplicationInstance.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "This application cannot be submitted until every selected "
            "prerequisite is approved.",
        ),
    )

    def __init__(self, *args, product, user, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["dependencies"].queryset = (
            ApplicationInstance.objects.visible_to(user)
            .filter(product=product)
            .order_by("-updated_at", "-pk")
        )


class ApplicationFilterForm(forms.Form):
    status = forms.ChoiceField(label=_("Status"), required=False)
    application_type = forms.ChoiceField(label=_("Application type"), required=False)
    product = forms.ModelChoiceField(
        label=_("Product"),
        queryset=Product.objects.none(),
        required=False,
        empty_label=_("All products"),
    )
    query_state = forms.ChoiceField(
        label=_("Query state"),
        required=False,
        choices=[
            ("", _("All query states")),
            ("pending", _("Pending queries")),
            ("clear", _("No pending queries")),
        ],
    )
    q = forms.CharField(
        label=_("Search"),
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={"placeholder": _("Reference, product, or organisation")},
        ),
    )

    def __init__(self, *args, product_queryset=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        status_labels = {}
        for definition in registry.all():
            for status in definition.statuses:
                status_labels.setdefault(status.key, status.label)
        self.fields["status"].choices = [
            ("", _("All statuses")),
            *status_labels.items(),
        ]
        self.fields["application_type"].choices = [
            ("", _("All application types")),
            *[(definition.key, definition.name) for definition in registry.all()],
        ]
        self.fields["product"].queryset = (
            product_queryset if product_queryset is not None else Product.objects.none()
        )

    def selected(self) -> dict[str, object]:
        if not self.is_valid():
            return {}
        return {key: value for key, value in self.cleaned_data.items() if value}


class QueryReplyForm(forms.Form):
    body = forms.CharField(
        label=_("Reply"),
        widget=forms.Textarea(
            attrs={"rows": 5, "placeholder": _("Write a clear response")},
        ),
    )


class ApplicationAccessForm(forms.Form):
    user = UserChoiceField(
        label=_("User"),
        queryset=get_user_model().objects.none(),
    )
    role = forms.ChoiceField(label=_("Application role"))
    direct_permissions = forms.MultipleChoiceField(
        label=_("Additional permissions"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Optional additions to the selected role."),
    )

    def __init__(self, *args, application, actor, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.application = application
        self.actor = actor
        roles = assignable_roles(application, actor)
        self.roles = roles
        self.fields["role"].choices = [(role.key, role.label) for role in roles]

        audiences = {role.audience for role in roles}
        query = Q(pk__in=[])
        if "organisation" in audiences:
            query |= Q(memberships__organisation=application.organisation)
        if "platform" in audiences:
            query |= Q(is_ohc_team=True)
        self.fields["user"].queryset = (
            get_user_model()
            .objects.filter(query)
            .exclude(pk=application.created_by_id)
            .distinct()
            .order_by("name", "email")
        )

        definition = registry.get(application.application_type)
        audience_permission_keys = (
            set().union(
                *(
                    role.permissions
                    for role in definition.roles
                    if role.audience in audiences
                ),
            )
            if audiences
            else set()
        )
        actor_permission_keys = get_effective_access(application, actor).permissions
        allowed_permission_keys = audience_permission_keys & actor_permission_keys
        self.fields["direct_permissions"].choices = [
            (permission.key, permission.label)
            for permission in definition.permissions
            if permission.key in allowed_permission_keys
        ]


class InternalNoteForm(forms.Form):
    body = forms.CharField(
        label=_("Internal note"),
        widget=forms.Textarea(attrs={"rows": 4}),
    )
