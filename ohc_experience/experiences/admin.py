from django.contrib import admin

from .models import ApplicationAccess
from .models import ApplicationAttachment
from .models import ApplicationEvent
from .models import ApplicationFormSubmission
from .models import ApplicationInstance
from .models import ApplicationQueryMessage
from .models import ApplicationQueryThread


class SubmissionInline(admin.TabularInline):
    model = ApplicationFormSubmission
    extra = 0
    readonly_fields = ["form_key", "revision", "submitted_by", "updated_at"]


class AccessInline(admin.TabularInline):
    model = ApplicationAccess
    extra = 0
    autocomplete_fields = ["user", "granted_by"]


@admin.register(ApplicationInstance)
class ApplicationInstanceAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "application_type",
        "organisation",
        "status",
        "created_by",
        "updated_at",
    ]
    list_filter = ["application_type", "status"]
    search_fields = ["reference", "title", "organisation__name"]
    autocomplete_fields = ["organisation", "created_by", "decided_by"]
    readonly_fields = ["reference", "created_at", "updated_at"]
    inlines = [SubmissionInline, AccessInline]


@admin.register(ApplicationEvent)
class ApplicationEventAdmin(admin.ModelAdmin):
    list_display = ["application", "kind", "title", "actor", "created_at"]
    list_filter = ["kind", "application__application_type"]
    search_fields = ["application__reference", "title", "description"]
    readonly_fields = [
        "application",
        "submission",
        "actor",
        "kind",
        "title",
        "description",
        "action_key",
        "status_before",
        "status_after",
        "payload",
        "created_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


admin.site.register(ApplicationAttachment)
admin.site.register(ApplicationQueryThread)
admin.site.register(ApplicationQueryMessage)
