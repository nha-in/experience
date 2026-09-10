from django.contrib import admin

from ohc_experience.experiences import permissions

from .models import Ticket
from .models import TicketMessage


class TicketMessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 0
    readonly_fields = ["created_at", "from_nha_team"]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return permissions.has_area(request.user, "support")

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (
            obj is None
            or permissions.visible_tickets(request.user).filter(pk=obj.pk).exists()
        )

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return self.has_change_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(pk__in=permissions.visible_tickets(request.user))
        )

    def get_inlines(self, request, obj):
        return self.inlines if request.user.is_superuser else []

    list_display = [
        "reference",
        "subject",
        "organisation",
        "product",
        "status",
        "priority",
        "assignee",
        "updated_at",
    ]
    list_filter = ["status", "priority", "category"]
    search_fields = ["reference", "subject", "organisation__name", "product__name"]
    autocomplete_fields = ["organisation", "assignee"]
    readonly_fields = [
        "reference",
        "created_at",
        "updated_at",
        "first_responded_at",
        "resolved_at",
    ]
    inlines = [TicketMessageInline]
