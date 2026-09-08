from django.contrib import admin

from .models import AuditEvent
from .models import EventRegistration
from .models import Milestone
from .models import Notification
from .models import ProductWorkspace
from .models import ReviewItem
from .models import ReviewQuery
from .models import SandboxCredential
from .models import TicketContext
from .services import assign_review


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


@admin.register(SandboxCredential)
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
