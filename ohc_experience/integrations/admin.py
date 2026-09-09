"""Read-only: the ledger is the chain's idempotency backstop, so an operator
correcting a row by hand would let the chain create a duplicate client."""

from __future__ import annotations

from django.contrib import admin

from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisioningRun


@admin.register(ProvisionedResource)
class ProvisionedResourceAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "product",
        "system",
        "external_ref",
        "public_ref",
        "state",
    )
    list_filter = ("system", "state")
    search_fields = ("external_ref", "public_ref", "product__name")
    list_select_related = ("product",)
    # `secret_ref` is a cache key to a live secret; it has no place on a screen.
    exclude = ("secret_ref",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ProvisioningRun)
class ProvisioningRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "product", "status", "finished_at", "correlation_id")
    list_filter = ("status",)
    search_fields = ("correlation_id", "product__name")
    list_select_related = ("product",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
