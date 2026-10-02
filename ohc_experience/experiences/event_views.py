from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION
from django.contrib.admin.models import CHANGE
from django.contrib.admin.models import DELETION
from django.contrib.admin.models import LogEntry
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from ohc_experience.events_and_activities.models import Event
from ohc_experience.organisations.models import Membership

from . import permissions
from . import tables
from .models import EventRegistration
from .models import Notification
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
            "kind",
            "category",
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


def duplicate_source(request):
    """The event that New event, opened as its duplicate, is filled in from."""
    try:
        pk = int(request.GET.get("from", ""))
    except ValueError:
        return None
    return permissions.visible_events(request.user).filter(pk=pk).first()


def duplicate_initial(source):
    """The form's answers taken from `source`, all but its dates and times.

    A duplicate needs dates of its own. Publication, the slug and the author are
    not on the form, so a duplicate starts unpublished and gets its own.
    """
    return {
        name: getattr(source, name)
        for name in EventForm.Meta.fields
        if name not in {"starts_at", "ends_at"}
    }


def event_log(actor, event, action, *, flag=CHANGE):
    LogEntry.objects.create(
        user=actor,
        content_type=ContentType.objects.get_for_model(event),
        object_id=str(event.pk),
        object_repr=event.title,
        action_flag=flag,
        change_message=action,
    )


PARTICIPANT_COLUMNS = {
    "name": (Lower("user__name"), Lower("user__email")),
    "organisation": Lower("organisation_name"),
    "mobile": "user__phone_number",
    "registered": "created_at",
}

PARTICIPANT_EXPORT_HEADER = (
    "Name",
    "Email",
    "Organisation",
    "Role",
    "Mobile",
    "Registered on",
)


def participants(event, search):
    """Registrations for the team that runs the event, with each organisation's name."""
    # One organisation per account today, as get_membership_for assumes.
    organisation = Membership.objects.filter(user=OuterRef("user")).values(
        name=tables.organisation_name("organisation__"),
    )
    query = (
        event.registrations.select_related("user")
        .prefetch_related("user__memberships__organisation")
        .annotate(organisation_name=Subquery(organisation[:1]))
    )
    if search:
        query = query.filter(
            Q(user__name__icontains=search)
            | Q(user__email__icontains=search)
            | Q(user__memberships__organisation__name__icontains=search)
            | Q(user__memberships__organisation__legal_name__icontains=search),
        ).distinct()
    return query


def with_memberships(registrations):
    for registration in registrations:
        registration.membership = next(
            iter(registration.user.memberships.all()),
            None,
        )
    return registrations


def participant_export_rows(registrations):
    for registration in with_memberships(registrations):
        membership = registration.membership
        yield (
            registration.user.display_name,
            registration.user.email,
            membership.organisation.display_name if membership else "",
            membership.get_role_display() if membership else "",
            registration.user.phone_number,
            f"{timezone.localtime(registration.created_at):%d/%m/%Y %H:%M}",
        )


def update_registration(request, event):
    """Take the signed-in account's registration, or cancel the one it holds."""
    if not event.is_published or event.is_past:
        raise PermissionDenied
    if request.POST.get("intent") == "cancel":
        deleted, _details = EventRegistration.objects.filter(
            event=event,
            user=request.user,
        ).delete()
        if deleted:
            messages.success(request, f"Registration cancelled for {event.title}.")
    else:
        _registration, created = EventRegistration.objects.get_or_create(
            event=event,
            user=request.user,
        )
        if created:
            messages.success(request, f"You are registered for {event.title}.")
            Notification.objects.create(
                recipient=request.user.email,
                subject=f"{get_program().short_name}: registered for {event.title}",
                body=(
                    f"{event.title}\n"
                    f"{timezone.localtime(event.starts_at):%d/%m/%Y, %H:%M %Z}\n"
                    f"{event.join_url}"
                ),
            )
    return redirect("experiences:event-detail", pk=event.pk)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def event_detail(request, pk):
    """One event for both audiences: its details, its actions and its participants.

    Anyone who reaches a published event can register for it; the team that runs
    the event reaches its drafts too, and reads the participants underneath.
    """
    permissions.require_area(request.user, "events")
    event = get_object_or_404(permissions.visible_events(request.user), pk=pk)
    if request.method == "POST":
        return update_registration(request, event)
    can_manage = permissions.has_area(request.user, "events")
    search = request.GET.get("q", "").strip()
    sort, page = "", None
    if can_manage:
        sort, order = tables.sorting(request, PARTICIPANT_COLUMNS, "-registered")
        registrations = participants(event, search).order_by(*order)
        if export := tables.export_format(request):
            return tables.export_response(
                export,
                f"participants-{event.pk}",
                PARTICIPANT_EXPORT_HEADER,
                participant_export_rows(registrations),
            )
        page = tables.paginate(request, registrations, 25)
        with_memberships(page)
    return render(
        request,
        "experiences/event_detail.html",
        {
            "nav": "events",
            "page_title": event.title,
            "event": event,
            "can_manage": can_manage,
            "can_edit": can_edit_event(request.user, event),
            "can_publish": permissions.has_access(
                request.user,
                "events",
                event.category,
                "approve",
                event.program,
            ),
            "can_register": event.is_published and not event.is_past,
            "registered": event.registrations.filter(user=request.user).exists(),
            "registration_count": event.registrations.count(),
            "page": page,
            "search": search,
            "table_sort": sort,
        },
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def event_edit(request, pk=None):
    if not permissions.has_area(request.user, "events", "write"):
        raise PermissionDenied
    source = None if pk else duplicate_source(request)
    with transaction.atomic():
        event = (
            get_object_or_404(
                permissions.visible_events(request.user).select_for_update(),
                pk=pk,
            )
            if pk
            else Event(program=source.program)
            if source
            else Event()
        )
        if pk and not can_edit_event(request.user, event):
            raise PermissionDenied
        form = EventForm(
            request.POST or None,
            instance=event,
            initial=duplicate_initial(source) if source else None,
            actor=request.user,
        )
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
            return redirect("experiences:event-detail", pk=event.pk)
    return render(
        request,
        "experiences/event_edit.html",
        {
            "nav": "events",
            "page_title": "Edit event" if pk else "New event",
            "form": form,
            "source": source,
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
    return redirect("experiences:event-detail", pk=event.pk)


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
    return redirect("experiences:events")
