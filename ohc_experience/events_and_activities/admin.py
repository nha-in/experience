from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction

from ohc_experience.experiences import permissions
from ohc_experience.experiences.admin_access import ProgramCategoryForm

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "program",
        "category",
        "kind",
        "starts_at",
        "published_at",
        "created_by",
    ]
    list_filter = ["program", "category", "kind", "published_at"]
    search_fields = ["title", "summary"]
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ["created_by", "published_at"]
    actions = ["publish_events", "unpublish_events"]

    def has_module_permission(self, request):
        return permissions.has_area(request.user, "events")

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (
            obj is None
            or permissions.visible_events(request.user).filter(pk=obj.pk).exists()
        )

    def has_add_permission(self, request):
        return permissions.has_area(request.user, "events", "write")

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request) and (
            obj is None
            or (
                permissions.visible_events(request.user, "write")
                .filter(pk=obj.pk)
                .exists()
                and (request.user.is_superuser or not obj.is_published)
            )
        )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_publish_permission(self, request):
        return permissions.has_area(request.user, "events", "approve")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(pk__in=permissions.visible_events(request.user))
        )

    def get_form(self, request, obj=None, **kwargs):
        class EventForm(ProgramCategoryForm):
            def clean(self):
                data = super().clean()
                if "program" in data and not permissions.has_access(
                    request.user,
                    "events",
                    data.get("category", ""),
                    "write",
                    data["program"],
                ):
                    self.add_error(
                        "category",
                        "You do not have write access to this category.",
                    )
                return data

        kwargs["form"] = EventForm
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        if not permissions.has_access(
            request.user,
            "events",
            obj.category,
            "write",
            obj.program,
        ):
            raise PermissionDenied
        if change:
            previous = Event.objects.get(pk=obj.pk)
            if not self.has_change_permission(request, previous):
                raise PermissionDenied
            obj.published_at = previous.published_at
        else:
            obj.published_at = None
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @transaction.atomic
    def set_publication(self, request, queryset, *, publish):
        for event in queryset.select_for_update():
            if not permissions.has_access(
                request.user,
                "events",
                event.category,
                "approve",
                event.program,
            ):
                raise PermissionDenied
            event.publish() if publish else event.unpublish()
            event.save(update_fields=["published_at", "updated_at"])
            self.log_change(request, event, "Published" if publish else "Unpublished")

    @admin.action(description="Publish selected events", permissions=["publish"])
    def publish_events(self, request, queryset):
        self.set_publication(request, queryset, publish=True)

    @admin.action(description="Unpublish selected events", permissions=["publish"])
    def unpublish_events(self, request, queryset):
        self.set_publication(request, queryset, publish=False)
