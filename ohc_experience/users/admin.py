from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import admin as auth_admin
from django.contrib.auth import decorators
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import path
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .forms import OhcTeamCreationForm
from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm

if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    # Force the `admin` sign in process to go through the `django-allauth` workflow:
    # https://docs.allauth.org/en/latest/common/admin.html#admin
    admin.autodiscover()
    admin.site.login = decorators.login_required(admin.site.login)  # type: ignore[method-assign]

User = get_user_model()


class OhcTeamFilter(admin.SimpleListFilter):
    """Split the user list into the two populations that are managed differently."""

    title = _("account type")
    parameter_name = "population"

    def lookups(self, request, model_admin):
        return [
            ("ohc", _("OHC team")),
            ("vendor", _("Vendor users")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "ohc":
            return queryset.filter(is_ohc_team=True)
        if self.value() == "vendor":
            return queryset.filter(is_ohc_team=False)
        return queryset


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("name", "phone_number")}),
        (
            _("OHC team"),
            {
                "fields": ("is_ohc_team",),
                "description": _(
                    "OHC team members work the support queue across every vendor and "
                    "publish events. This is separate from staff status, which only "
                    "controls access to this admin.",
                ),
            },
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    list_display = [
        "email",
        "name",
        "account_type",
        "organisation_names",
        "is_active",
        "is_staff",
    ]
    list_filter = [OhcTeamFilter, "is_active", "is_staff", "is_superuser"]
    search_fields = ["name", "email"]
    ordering = ["email"]
    actions = ["grant_ohc_team", "revoke_ohc_team"]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "password1", "password2", "is_ohc_team"),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request).prefetch_related("memberships__organisation")
        )

    @admin.display(description=_("Account type"), ordering="is_ohc_team")
    def account_type(self, obj) -> str:
        return _("OHC team") if obj.is_ohc_team else _("Vendor")

    @admin.display(description=_("Organisations"))
    def organisation_names(self, obj) -> str:
        names = [m.organisation.name for m in obj.memberships.all()]
        return ", ".join(names) if names else "—"

    def get_urls(self):
        """Add a dedicated "add OHC team member" screen next to the normal one."""
        extra = [
            path(
                "add-ohc-member/",
                self.admin_site.admin_view(self.add_ohc_member_view),
                name="users_user_add_ohc_member",
            ),
        ]
        return extra + super().get_urls()

    def add_ohc_member_view(self, request):
        """A cut-down add form that always produces an OHC team account."""
        if not request.user.is_superuser:
            messages.error(request, _("Only superusers can add OHC team members."))
            return redirect(reverse("admin:users_user_changelist"))

        if request.method == "POST":
            form = OhcTeamCreationForm(request.POST)
            if form.is_valid():
                user = form.save()
                messages.success(
                    request,
                    _(
                        "%(email)s can now work the support queue and publish events.",
                    )
                    % {"email": user.email},
                )
                return redirect(
                    reverse("admin:users_user_change", args=[user.pk]),
                )
        else:
            form = OhcTeamCreationForm()

        context = {
            **self.admin_site.each_context(request),
            "title": _("Add OHC team member"),
            "form": form,
            "opts": self.model._meta,  # noqa: SLF001
        }
        return render(request, "admin/users/add_ohc_member.html", context)

    @admin.action(description=_("Grant OHC team access"))
    def grant_ohc_team(self, request, queryset):
        updated = queryset.update(is_ohc_team=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d account now has OHC team access.",
                "%(count)d accounts now have OHC team access.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Revoke OHC team access"))
    def revoke_ohc_team(self, request, queryset):
        updated = queryset.update(is_ohc_team=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d account no longer has OHC team access.",
                "%(count)d accounts no longer have OHC team access.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )
