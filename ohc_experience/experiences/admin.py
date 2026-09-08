from django.contrib import admin

from .models import ApplicationDependency
from .models import ApplicationFormUse
from .models import ApplicationInstance
from .models import FormAttachment
from .models import FormRecord
from .models import FormSubmission
from .models import Product
from .models import ProductOutcome


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
