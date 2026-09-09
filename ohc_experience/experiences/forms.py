from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .fields import MultipleFileField
from .permissions import get_effective_access
from .registry import registry
from .services import assignable_roles


class ExperienceForm(forms.Form):
    """The base class for every form a definition registers.

    ``ApplicationFormDefinition.build_form`` hands the workflow context and the
    submission's current attachments in as keyword arguments, and
    ``services.save_form_submission`` reads ``removed_file_ids`` back, so a
    static Django form can take part in the workflow without importing any of
    it.
    """

    def __init__(
        self,
        *args,
        experience_context=None,
        existing_files=None,
        **kwargs,
    ) -> None:
        self.experience_context = experience_context
        self.existing_files = existing_files or {}
        super().__init__(*args, **kwargs)
        self.removed_file_ids = {
            field_name: self._posted_removals(field_name)
            for field_name, field in self.fields.items()
            if isinstance(field, forms.FileField)
        }

    def _posted_removals(self, field_name: str) -> set[int]:
        key = f"remove_files__{field_name}"
        values = (
            self.data.getlist(key)
            if hasattr(self.data, "getlist")
            else self.data.get(key, [])
        )
        if not isinstance(values, (list, tuple)):
            values = [values]
        result = set()
        for value in values:
            try:
                result.add(int(value))
            except TypeError, ValueError:
                continue
        return result

    def retained_existing_files(self, field_name: str):
        removed_ids = self.removed_file_ids.get(field_name, set())
        return [
            attachment
            for attachment in self.existing_files.get(field_name, [])
            if attachment.pk not in removed_ids
        ]

    def clean(self):
        cleaned = super().clean()
        for field_name, field in self.fields.items():
            if not isinstance(field, MultipleFileField) or field_name in self.errors:
                continue
            existing_count = len(self.retained_existing_files(field_name))
            upload_count = len(cleaned.get(field_name) or [])
            final_count = existing_count + upload_count
            if final_count < field.min_files:
                self.add_error(
                    field_name,
                    ValidationError(
                        field.error_messages["too_few_files"],
                        code="too_few_files",
                        params={"minimum": field.min_files},
                    ),
                )
            elif field.max_files is not None and final_count > field.max_files:
                self.add_error(
                    field_name,
                    ValidationError(
                        field.error_messages["too_many_files"],
                        code="too_many_files",
                        params={"maximum": field.max_files},
                    ),
                )
        return cleaned

    def require_upload(self, field_name: str, label: str) -> None:
        has_upload = self.cleaned_data.get(field_name) or self.retained_existing_files(
            field_name,
        )
        if not has_upload and not self.has_error(field_name):
            self.add_error(
                field_name,
                _("Upload %(label)s before completing this form.") % {"label": label},
            )


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj) -> str:
        return f"{obj.display_name} ({obj.email})"


class ApplicationFilterForm(forms.Form):
    status = forms.ChoiceField(label=_("Status"), required=False)
    application_type = forms.ChoiceField(label=_("Application type"), required=False)
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

    def __init__(self, *args, **kwargs) -> None:
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

    def selected(self) -> dict[str, str]:
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
