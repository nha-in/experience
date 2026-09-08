from django.contrib import admin

from .models import Event
from .models import EventRegistration


class RegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at", "reminded_at"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "starts_at", "published_at", "created_by"]
    list_filter = ["kind", "published_at"]
    search_fields = ["title", "summary"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["created_by"]
    inlines = [RegistrationInline]
