from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "starts_at", "published_at", "created_by"]
    list_filter = ["kind", "published_at"]
    search_fields = ["title", "summary"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["created_by"]
