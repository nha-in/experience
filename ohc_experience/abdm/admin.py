from django.contrib import admin

from .models import AuditLog
from .models import ComplianceRecord
from .models import Credential
from .models import Product
from .models import ReviewHistory
from .models import ReviewItem
from .models import ReviewQuery


class ComplianceInline(admin.TabularInline):
    model = ComplianceRecord
    extra = 0
    fields = ["track_code", "milestone_code", "status", "submitted_on", "approved_on"]
    readonly_fields = ["submitted_on", "approved_on"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "sandbox_id",
        "name",
        "organisation",
        "solution_type",
        "registration_status",
        "created_at",
    ]
    list_filter = ["registration_status", "solution_type", "category"]
    search_fields = ["sandbox_id", "name", "organisation__name"]
    autocomplete_fields = ["organisation", "created_by", "registered_by"]
    readonly_fields = ["sandbox_id", "created_at", "updated_at"]
    inlines = [ComplianceInline]


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    list_display = ["client_id", "product", "issued_on", "rotation_due", "revoked_on"]
    search_fields = ["client_id", "product__sandbox_id", "product__name"]
    autocomplete_fields = ["product"]
    # The secret is never shown in the admin, encrypted or otherwise.
    exclude = ["secret_encrypted"]
    readonly_fields = [
        "issued_on",
        "rotated_on",
        "callback_status_code",
        "callback_latency_ms",
        "callback_checked_at",
        "callback_error",
        "callback_failure_streak",
    ]


class QueryInline(admin.TabularInline):
    model = ReviewQuery
    extra = 0
    readonly_fields = ["raised_at", "replied_at", "resolved_at"]


class HistoryInline(admin.TabularInline):
    model = ReviewHistory
    extra = 0
    can_delete = False
    readonly_fields = ["actor", "kind", "title", "description", "created_at"]

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ReviewItem)
class ReviewItemAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "item_type",
        "organisation",
        "product",
        "status",
        "assignee",
        "submitted_on",
    ]
    list_filter = ["item_type", "status"]
    search_fields = ["reference", "organisation__name", "product__name"]
    autocomplete_fields = ["organisation", "product", "assignee", "decided_by"]
    readonly_fields = ["reference", "created_at", "updated_at"]
    inlines = [QueryInline, HistoryInline]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "action", "model_label", "object_repr"]
    list_filter = ["model_label", "action"]
    search_fields = ["object_repr", "object_pk"]
    readonly_fields = [
        "actor",
        "model_label",
        "object_pk",
        "object_repr",
        "action",
        "diff",
        "created_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


admin.site.register(ComplianceRecord)
