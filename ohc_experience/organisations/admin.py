from django.contrib import admin

from ohc_experience.experiences.admin_access import SuperuserAdminMixin

from .models import Invitation
from .models import Membership
from .models import Organisation


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Organisation)
class OrganisationAdmin(SuperuserAdminMixin, admin.ModelAdmin):
    list_display = ["name", "verification_status", "onboarded_at", "created_at"]
    list_filter = ["verification_status"]
    search_fields = ["name", "legal_name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(SuperuserAdminMixin, admin.ModelAdmin):
    list_display = ["user", "organisation", "role", "joined_at"]
    list_filter = ["role"]
    search_fields = ["user__email", "user__name", "organisation__name"]
    autocomplete_fields = ["user", "organisation"]


@admin.register(Invitation)
class InvitationAdmin(SuperuserAdminMixin, admin.ModelAdmin):
    list_display = ["email", "organisation", "role", "created_at", "accepted_at"]
    list_filter = ["role"]
    search_fields = ["email", "organisation__name"]
