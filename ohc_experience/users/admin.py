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

from ohc_experience.experiences.admin_access import AccessGrantInline
from ohc_experience.experiences.admin_access import SuperuserAdminMixin

from .forms import NhaTeamCreationForm
from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm

if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    # Force the `admin` sign in process to go through the `django-allauth` workflow:
    # https://docs.allauth.org/en/latest/common/admin.html#admin
    admin.autodiscover()
    admin.site.login = decorators.login_required(admin.site.login)  # type: ignore[method-assign]

User = get_user_model()


class NhaTeamFilter(admin.SimpleListFilter):
    """Split the user list into the two populations that are managed differently."""

    title = _("account type")
    parameter_name = "population"

    def lookups(self, request, model_admin):
        return [
            ("nha", _("NHA team")),
            ("integrator", _("Integrator users")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "nha":
            return queryset.filter(is_nha_team=True)
        if self.value() == "integrator":
            return queryset.filter(is_nha_team=False)
        return queryset


@admin.register(User)
class UserAdmin(SuperuserAdminMixin, auth_admin.UserAdmin):
    inlines = [AccessGrantInline]

    def get_inlines(self, request, obj):
        return self.inlines if obj else []

    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("name", "phone_number", "phone_verified")}),
        (
            _("NHA team"),
            {
                "fields": ("is_nha_team",),
                "description": _(
                    "Identifies a staff account. Assign review, support, and event "
                    "access separately under Portal permissions below. Staff status "
                    "only controls access to this admin.",
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
    list_filter = [NhaTeamFilter, "is_active", "is_staff", "is_superuser"]
    search_fields = ["name", "email"]
    ordering = ["email"]
    actions = ["grant_nha_team", "revoke_nha_team"]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "password1", "password2", "is_nha_team"),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request).prefetch_related("memberships__organisation")
        )

    @admin.display(description=_("Account type"), ordering="is_nha_team")
    def account_type(self, obj) -> str:
        return _("NHA team") if obj.is_nha_team else _("Integrator")

    @admin.display(description=_("Organisations"))
    def organisation_names(self, obj) -> str:
        names = [m.organisation.name for m in obj.memberships.all()]
        return ", ".join(names) if names else "—"

    def get_urls(self):
        """Add a dedicated "add NHA team member" screen next to the normal one."""
        extra = [
            path(
                "add-nha-member/",
                self.admin_site.admin_view(self.add_nha_member_view),
                name="users_user_add_nha_member",
            ),
        ]
        return extra + super().get_urls()

    def add_nha_member_view(self, request):
        """A cut-down add form that always produces an NHA team account."""
        if not request.user.is_superuser:
            messages.error(request, _("Only superusers can add NHA team members."))
            return redirect(reverse("admin:users_user_changelist"))

        if request.method == "POST":
            form = NhaTeamCreationForm(request.POST)
            if form.is_valid():
                user = form.save()
                messages.success(
                    request,
                    _(
                        "%(email)s was created. Assign portal permissions below.",
                    )
                    % {"email": user.email},
                )
                return redirect(
                    reverse("admin:users_user_change", args=[user.pk]),
                )
        else:
            form = NhaTeamCreationForm()

        context = {
            **self.admin_site.each_context(request),
            "title": _("Add NHA team member"),
            "form": form,
            "opts": self.model._meta,  # noqa: SLF001
        }
        return render(request, "admin/users/add_nha_member.html", context)

    @admin.action(description=_("Grant NHA team access"))
    def grant_nha_team(self, request, queryset):
        updated = queryset.update(is_nha_team=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d account now has NHA team access.",
                "%(count)d accounts now have NHA team access.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Revoke NHA team access"))
    def revoke_nha_team(self, request, queryset):
        updated = queryset.update(is_nha_team=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d account no longer has NHA team access.",
                "%(count)d accounts no longer have NHA team access.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )
