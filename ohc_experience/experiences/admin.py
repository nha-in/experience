from django.contrib import admin

from .models import ApplicationDependency
from .models import ApplicationFormUse
from .models import ApplicationInstance
from .models import AuditEvent
from .models import EventRegistration
from .models import FormAttachment
from .models import FormRecord
from .models import FormSubmission
from .models import Milestone
from .models import Notification
from .models import Product
from .models import ProductCredential
from .models import ProductOutcome
from .models import ProductWorkspace
from .models import ReviewItem
from .models import ReviewQuery
from .models import TicketContext
from .workflows import assign_review


class SubmissionInline(admin.TabularInline):
    model = FormSubmission
    extra = 0
    readonly_fields = ["form_key", "revision", "submitted_by", "updated_at"]


class FormUseInline(admin.TabularInline):
    model = ApplicationFormUse
    extra = 0
    autocomplete_fields = ["form", "selected_submission"]


class ProductOutcomeInline(admin.TabularInline):
    model = ProductOutcome
    extra = 0
    readonly_fields = ["outcome_type", "name", "source_application", "issued_at"]


class DependencyInline(admin.TabularInline):
    model = ApplicationDependency
    fk_name = "application"
    extra = 0
    autocomplete_fields = ["depends_on"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "product_type", "organisation", "updated_at"]
    list_filter = ["product_type"]
    search_fields = ["name", "organisation__name", "slug"]
    autocomplete_fields = ["organisation", "created_by"]
    readonly_fields = ["slug", "created_at", "updated_at"]
    inlines = [ProductOutcomeInline]


@admin.register(FormRecord)
class FormRecordAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "name",
        "form_key",
        "reuse_scope",
        "product",
        "organisation",
        "updated_at",
    ]
    list_filter = ["reuse_scope", "form_key"]
    search_fields = ["reference", "name", "product__name", "organisation__name"]
    autocomplete_fields = ["product", "organisation", "created_by"]
    readonly_fields = ["reference", "created_at", "updated_at"]
    inlines = [SubmissionInline]


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = [
        "form",
        "form_key",
        "submission_number",
        "revision",
        "is_current",
        "submitted_by",
        "updated_at",
    ]
    list_filter = ["form_key", "status", "is_current"]
    search_fields = ["form__reference", "form__name", "form_key"]
    autocomplete_fields = ["form", "origin_application", "submitted_by"]


@admin.register(ApplicationInstance)
class ApplicationInstanceAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "application_type",
        "product",
        "status",
        "created_by",
        "updated_at",
    ]
    list_filter = ["application_type", "status"]
    search_fields = [
        "reference",
        "title",
        "product__name",
        "product__organisation__name",
    ]
    autocomplete_fields = ["product", "created_by", "decided_by"]
    readonly_fields = ["reference", "created_at", "updated_at"]
    inlines = [FormUseInline, DependencyInline]


admin.site.register(FormAttachment)
admin.site.register(ProductOutcome)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReviewItem)
class ReviewItemAdmin(ReadOnlyAdmin):
    list_display = ("reference", "kind", "title", "status", "assignee", "submitted_at")
    list_filter = ("kind", "status", "assignee")
    search_fields = ("organisation__name", "product__name", "application__reference")
    readonly_fields = tuple(
        field.name
        for field in ReviewItem._meta.fields  # noqa: SLF001
        if field.name != "assignee"
    )

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        if change:
            assign_review(obj, request.user, obj.assignee)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "assignee":
            from django.contrib.auth import get_user_model  # noqa: PLC0415
            from django.db.models import Q  # noqa: PLC0415

            kwargs["queryset"] = get_user_model().objects.filter(
                Q(is_ohc_team=True) | Q(is_superuser=True),
                is_active=True,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ProductCredential)
class CredentialAdmin(ReadOnlyAdmin):
    list_display = ("product", "client_id", "status", "issued_at", "rotation_due")
    exclude = ("encrypted_secret",)


@admin.register(AuditEvent)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ("action", "actor", "organisation", "product", "created_at")
    list_filter = ("action",)


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ("recipient", "subject", "sent_at", "attempts", "last_error")


for model in (
    ProductWorkspace,
    Milestone,
    ReviewQuery,
    EventRegistration,
    TicketContext,
):
    admin.site.register(model, ReadOnlyAdmin)
