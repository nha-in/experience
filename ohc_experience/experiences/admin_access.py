from django import forms
from django.contrib import admin

from .models import AccessGrant
from .registry import registry


class SuperuserAdminMixin:
    """Raw workflow records and access grants must not bypass portal policies."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_queryset(self, request):
        query = super().get_queryset(request)
        return query if self.has_module_permission(request) else query.none()


class ProgramCategoryForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        programs = registry.programs()
        self.fields["program"] = forms.ChoiceField(
            choices=[(program.key, program.short_name) for program in programs],
        )
        # A grant's vocabulary depends on the area picked on this same form, so
        # the field offers every area's categories and clean() narrows them.
        categories = {}
        for program in programs:
            for area in self._areas():
                for code, label, _description in program.grant_categories(area):
                    if code:
                        categories.setdefault(code, label)
        choices = [("", "General / onboarding")]
        if self._meta.model is AccessGrant:
            choices.append(("*", "All categories"))
        choices.extend(categories.items())
        self.fields["category"] = forms.ChoiceField(choices=choices, required=False)

    def _areas(self):
        """Areas this form can name a category for. Events only ever mean tracks."""
        return ["review", "support"] if self._meta.model is AccessGrant else ["events"]

    def clean(self):
        data = super().clean()
        programs = {program.key: program for program in registry.programs()}
        program = programs.get(data.get("program"))
        area = data.get("area") if self._meta.model is AccessGrant else "events"
        allowed = {
            code
            for code, _label, _description in (
                program.grant_categories(area) if program else ()
            )
        } | {""}
        if self._meta.model is AccessGrant:
            allowed.add("*")
        if data.get("category") not in allowed:
            self.add_error("category", "Choose a category in the selected program.")
        return data


class AccessGrantForm(ProgramCategoryForm):
    class Meta:
        model = AccessGrant
        fields = [
            "user",
            "program",
            "area",
            "category",
            "can_read",
            "can_write",
            "can_approve",
        ]


class AccessGrantInline(SuperuserAdminMixin, admin.TabularInline):
    model = AccessGrant
    form = AccessGrantForm
    extra = 0
    verbose_name = "Portal permission"
    verbose_name_plural = "Portal permissions (staff accounts only)"

    def has_add_permission(self, request, obj=None):
        return self.has_module_permission(request)
