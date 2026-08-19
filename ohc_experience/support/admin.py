from django.contrib import admin

from .models import Ticket
from .models import TicketMessage


class TicketMessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 0
    readonly_fields = ["created_at", "from_ohc_team"]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "subject",
        "organisation",
        "status",
        "priority",
        "assignee",
        "updated_at",
    ]
    list_filter = ["status", "priority", "category"]
    search_fields = ["reference", "subject", "organisation__name"]
    autocomplete_fields = ["organisation", "assignee"]
    readonly_fields = [
        "reference",
        "created_at",
        "updated_at",
        "first_responded_at",
        "resolved_at",
    ]
    inlines = [TicketMessageInline]
