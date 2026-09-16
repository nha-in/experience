from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION
from django.contrib.admin.models import CHANGE
from django.contrib.admin.models import DELETION
from django.contrib.admin.models import LogEntry
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from ohc_experience.events.models import Event

from . import permissions
from .registry import get_program


def can_edit_event(user, event):
    return permissions.has_access(
        user,
        "events",
        event.category,
        "write",
        event.program,
    ) and (user.is_superuser or not event.is_published)


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title",
            "category",
            "kind",
            "summary",
            "description",
            "starts_at",
            "ends_at",
            "location",
            "join_url",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "ends_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        program = get_program(self.instance.program)
        choices = [
            ("", "General / onboarding"),
            *((track.code, track.code) for track in program.tracks),
        ]
        self.fields["category"] = forms.ChoiceField(
            required=False,
            choices=[
                (key, label)
                for key, label in choices
                if permissions.has_access(actor, "events", key, "write", program.key)
            ],
        )


def event_log(actor, event, action, *, flag=CHANGE):
    LogEntry.objects.create(
        user=actor,
        content_type=ContentType.objects.get_for_model(event),
        object_id=str(event.pk),
        object_repr=event.title,
        action_flag=flag,
        change_message=action,
    )


@login_required
@never_cache
def event_manage(request):
    if not permissions.has_area(request.user, "events"):
        raise PermissionDenied
    query = (
        permissions.visible_events(request.user)
        .annotate(registration_count=Count("registrations"))
        .order_by("-starts_at", "-pk")
    )
    status = request.GET.get("status", "all")
    if status in {"draft", "published"}:
        query = query.filter(published_at__isnull=status == "draft")
    else:
        status = "all"
    page = Paginator(query, 20).get_page(request.GET.get("page"))
    for event in page:
        event.can_edit = can_edit_event(request.user, event)
        event.can_publish = permissions.has_access(
            request.user,
            "events",
            event.category,
            "approve",
            event.program,
        )
    return render(
        request,
        "experiences/event_manage.html",
        {
            "nav": "events",
            "page_title": "Manage events",
            "page": page,
            "status": status,
        },
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def event_edit(request, pk=None):
    if not permissions.has_area(request.user, "events", "write"):
        raise PermissionDenied
    with transaction.atomic():
        event = (
            get_object_or_404(
                permissions.visible_events(request.user).select_for_update(),
                pk=pk,
            )
            if pk
            else Event()
        )
        if pk and not can_edit_event(request.user, event):
            raise PermissionDenied
        form = EventForm(request.POST or None, instance=event, actor=request.user)
        if request.method == "POST" and form.is_valid():
            if not permissions.has_access(
                request.user,
                "events",
                form.cleaned_data["category"],
                "write",
                event.program,
            ):
                raise PermissionDenied
            event = form.save(commit=False)
            if not pk:
                event.created_by = request.user
                event.published_at = None
            event.save()
            event_log(
                request.user,
                event,
                "Event updated" if pk else "Event created",
                flag=CHANGE if pk else ADDITION,
            )
            messages.success(request, "Event saved.")
            return redirect("experiences:event-manage")
    return render(
        request,
        "experiences/event_edit.html",
        {
            "nav": "events",
            "page_title": "Edit event" if pk else "New event",
            "form": form,
        },
    )


@login_required
@never_cache
@require_POST
def event_publication(request, pk):
    with transaction.atomic():
        event = get_object_or_404(
            permissions.visible_events(request.user).select_for_update(),
            pk=pk,
        )
        if not permissions.has_access(
            request.user,
            "events",
            event.category,
            "approve",
            event.program,
        ):
            raise PermissionDenied
        action = request.POST.get("intent")
        if action not in {"publish", "unpublish"}:
            raise PermissionDenied
        event.publish() if action == "publish" else event.unpublish()
        event.save(update_fields=["published_at", "updated_at"])
        event_log(
            request.user,
            event,
            "Event published" if action == "publish" else "Event unpublished",
        )
    messages.success(
        request,
        "Event published." if action == "publish" else "Event unpublished.",
    )
    return redirect("experiences:event-manage")


@login_required
@never_cache
@require_POST
def event_delete(request, pk):
    with transaction.atomic():
        event = get_object_or_404(
            permissions.visible_events(request.user).select_for_update(),
            pk=pk,
        )
        # Same rule as editing: only a superuser can delete a published event.
        if not can_edit_event(request.user, event):
            raise PermissionDenied
        event_log(request.user, event, "Event deleted", flag=DELETION)
        event.delete()
    messages.success(request, "Event deleted.")
    return redirect("experiences:event-manage")
