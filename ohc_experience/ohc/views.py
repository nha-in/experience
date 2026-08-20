"""The OHC team's console: the support queue, the vendor register, and events.

Every view here inherits OhcConsoleMixin, and OhcConsoleMixin exists to make the
gate impossible to forget: it is OhcTeamRequiredMixin plus the shell's active
nav item, so there is no way to add a screen to this app without the check. That
gate is the security boundary of the whole feature — a signed-in vendor who
guesses a URL under /ohc/ is looking at every other vendor's tickets — so it is
also asserted route by route in tests/test_views.py.

State is never written here. post_reply() and record_status_change() own what a
reply and a move mean, and Organisation.set_verification() owns what verifying a
vendor means; the views collect the input and call them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.db import models
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView
from django.views.generic import ListView
from django.views.generic import UpdateView

from ohc_experience.events.models import Event
from ohc_experience.organisations.care_plugin import CarePluginClient, CarePluginError
from ohc_experience.organisations.models import Sandbox
from ohc_experience.organisations.tasks import provision_sandbox
from ohc_experience.organisations.models import Organisation
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply
from ohc_experience.support.models import record_status_change
from ohc_experience.users.permissions import OhcTeamRequiredMixin

from .forms import ASSIGNEE_MINE
from .forms import ASSIGNEE_UNASSIGNED
from .forms import EventForm
from .forms import OrganisationFilterForm
from .forms import TicketControlForm
from .forms import TicketFilterForm
from .forms import TicketReplyForm
from .forms import VerificationForm
from .forms import ohc_team_members

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

QUEUE_PAGE_SIZE = 25


class OhcConsoleMixin(OhcTeamRequiredMixin):
    """The gate on every console screen, plus which nav item is lit.

    Subclasses that are pure POST endpoints never call get_context_data; they
    still inherit this so that "is it gated?" is answered by the class list.
    """

    nav_section = ""

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = self.nav_section
        return context


# ── Support queue ──────────────────────────────────────────────────────────


class TicketQueueView(OhcConsoleMixin, ListView):
    """Every vendor's tickets in one list, filtered by status/priority/assignee.

    The filters are a plain GET form, so a filtered queue is a URL that can be
    pasted into a handover note. htmx only saves the page reload.
    """

    template_name = "ohc/queue.html"
    # The page includes this fragment; a filter change swaps the same file back
    # in, so a filtered list and a reloaded page are the same markup.
    partial_template_name = "ohc/partials/queue_results.html"
    context_object_name = "tickets"
    paginate_by = QUEUE_PAGE_SIZE
    nav_section = "queue"

    @cached_property
    def team(self):
        return list(ohc_team_members())

    @cached_property
    def filter_form(self) -> TicketFilterForm:
        """Always bound, and always with a status.

        With no status in the query string the queue shows the Care team's own
        work, so that default is written into the form's data rather than left
        implicit: the select then shows the filter that is actually applied,
        and the pagination links below carry it to page two.
        """
        data = self.request.GET.copy()
        if "status" not in data:
            data["status"] = Status.OPEN
        return TicketFilterForm(data, team=self.team)

    def get_queryset(self):
        tickets = Ticket.objects.with_related()
        status = self.filter_form.chosen("status")
        if status == Status.OPEN:
            # "Needs a reply" is exactly the queue's own work, and the queryset
            # already names it — better than restating the filter here.
            tickets = tickets.awaiting_ohc()
        elif status:
            tickets = tickets.filter(status=status)

        priority = self.filter_form.chosen("priority")
        if priority:
            tickets = tickets.filter(priority=priority)

        assignee = self.filter_form.chosen("assignee")
        if assignee == ASSIGNEE_UNASSIGNED:
            tickets = tickets.filter(assignee__isnull=True)
        elif assignee == ASSIGNEE_MINE:
            tickets = tickets.filter(assignee=self.request.user)
        elif assignee:
            tickets = tickets.filter(assignee_id=assignee)
        return tickets

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["filter_query"] = self.filter_query()
        context["showing_default_queue"] = self.showing_default_queue()
        return context

    def showing_default_queue(self) -> bool:
        """True when the queue is showing exactly its own work, unnarrowed.

        This is what the empty state turns on. "Nothing is waiting on the Care
        team" is only true of `awaiting_ohc()` with no other filter applied —
        said under a narrower filter it answers a question nobody asked, and on
        a triage screen "nothing is waiting" is the one sentence that must not
        be wrong.
        """
        return (
            self.filter_form.chosen("status") == Status.OPEN
            and not self.filter_form.chosen("priority")
            and not self.filter_form.chosen("assignee")
        )

    def filter_query(self) -> str:
        """The current filters as a query string, for the pagination links.

        Built from the form's data rather than from request.GET so that the
        default status travels with "page 2" — otherwise page two of the
        default queue would quietly widen to every status.
        """
        params = self.filter_form.data.copy()
        params.pop("page", None)
        return params.urlencode()

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        # A boosted nav click is also an htmx request, and it wants the whole
        # page; only the filter form asks for the results on their own.
        if request.htmx and not request.htmx.boosted:
            response.template_name = self.partial_template_name
        return response


def get_ticket(reference: str) -> Ticket:
    """One ticket, with the vendor and the people already loaded.

    Not scoped to an organisation on purpose — that is the difference between
    this console and the vendor inbox, and why the gate above matters so much.
    """
    return get_object_or_404(Ticket.objects.with_related(), reference=reference)


def workspace_context(
    ticket: Ticket,
    *,
    reply_form: TicketReplyForm | None = None,
    control_form: TicketControlForm | None = None,
) -> dict:
    """Context for the ticket screen, in the shape the partial expects.

    The full page and the two POST endpoints all render the same workspace
    fragment, so a swapped-in thread and a reloaded page cannot differ.

    The thread is under `thread`, never `messages` — that name belongs to the
    flash region, and shadowing it would swallow every alert on this page.
    """
    if control_form is None:
        control_form = TicketControlForm(
            initial={
                "status": ticket.status,
                "priority": ticket.priority,
                "assignee": ticket.assignee_id,
            },
        )
    return {
        "nav_section": "queue",
        "ticket": ticket,
        "thread": ticket.messages.select_related("author"),
        "reply_form": reply_form if reply_form is not None else TicketReplyForm(),
        "control_form": control_form,
    }


class TicketDetailView(OhcConsoleMixin, View):
    """Work one ticket: the conversation, the reply box and the control rail."""

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ticket = get_ticket(kwargs["reference"])
        return render(request, "ohc/ticket_detail.html", workspace_context(ticket))


class TicketActionView(OhcConsoleMixin, View):
    """Base for the console's two POST actions on a ticket.

    Both answer htmx with the workspace fragment — the thread, the reply box and
    the rail move together — and answer everyone else with POST → redirect →
    flash, which is the path that has to keep working with scripting off.
    """

    def swap(self, request: HttpRequest, ticket: Ticket, **forms) -> HttpResponse:
        if request.htmx:
            return render(
                request,
                "ohc/partials/ticket_workspace_swap.html",
                workspace_context(ticket, **forms),
            )
        return redirect("ohc:ticket", reference=ticket.reference)

    def reshow(self, request: HttpRequest, ticket: Ticket, **forms) -> HttpResponse:
        """A rejected submission: 200 with the errors, never a redirect."""
        context = workspace_context(ticket, **forms)
        if request.htmx:
            return render(request, "ohc/partials/ticket_workspace_swap.html", context)
        return render(request, "ohc/ticket_detail.html", context)


class TicketReplyView(TicketActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ticket = get_ticket(kwargs["reference"])
        form = TicketReplyForm(request.POST)
        if not form.is_valid():
            return self.reshow(request, ticket, reply_form=form)
        # post_reply moves the ticket to AWAITING_VENDOR and stamps the first
        # response time. The view does not touch ticket.status.
        post_reply(
            ticket,
            request.user,
            form.cleaned_data["body"],
            from_ohc_team=True,
        )
        # organisation.name, matching the queue table and the hint under the
        # reply box — one name for the vendor, everywhere in the console.
        messages.success(
            request,
            _("Reply sent to %(vendor)s.") % {"vendor": ticket.organisation.name},
        )
        return self.swap(request, ticket)


class TicketUpdateView(TicketActionView):
    """Status, priority and assignee, in one submission."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ticket = get_ticket(kwargs["reference"])
        form = TicketControlForm(request.POST)
        if not form.is_valid():
            return self.reshow(request, ticket, control_form=form)

        changed = self.apply_fields(ticket, form.cleaned_data)
        status = form.cleaned_data["status"]
        if ticket.status != status:
            # The move and the thread entry are one act, and the model owns it.
            record_status_change(ticket, request.user, status)
            changed = True

        if changed:
            messages.success(
                request,
                _("%(reference)s updated.") % {"reference": ticket.reference},
            )
        else:
            messages.info(request, _("Nothing on this ticket changed."))
        return self.swap(request, ticket)

    @staticmethod
    def apply_fields(ticket: Ticket, cleaned: dict) -> bool:
        """Save priority and assignee, and say whether anything moved."""
        fields = []
        if ticket.priority != cleaned["priority"]:
            ticket.priority = cleaned["priority"]
            fields.append("priority")
        if ticket.assignee != cleaned["assignee"]:
            ticket.assignee = cleaned["assignee"]
            fields.append("assignee")
        if fields:
            ticket.save(update_fields=[*fields, "updated_at"])
        return bool(fields)


# ── Vendor register ────────────────────────────────────────────────────────


class OrganisationListView(OhcConsoleMixin, ListView):
    """Every vendor on the platform, with the undecided ones first.

    Same idiom as the queue: the filters are a plain GET form, so a narrowed
    list is a URL, and htmx only saves the page reload.
    """

    template_name = "ohc/organisation_list.html"
    # The page includes this fragment; a filter change swaps the same file back
    # in, so a filtered list and a reloaded page are the same markup.
    partial_template_name = "ohc/partials/organisation_results.html"
    context_object_name = "organisations"
    paginate_by = QUEUE_PAGE_SIZE
    nav_section = "organisations"

    @cached_property
    def filter_form(self) -> OrganisationFilterForm:
        return OrganisationFilterForm(self.request.GET)

    def get_queryset(self):
        organisations = Organisation.objects.for_console()
        status = self.filter_form.chosen("status")
        if status:
            organisations = organisations.filter(verification_status=status)

        search = self.filter_form.chosen("q")
        if search:
            # Both names, because the console knows a vendor by the trading
            # name in the queue and by the legal entity on the certificate,
            # and whoever is searching has whichever one they were sent.
            organisations = organisations.filter(
                models.Q(name__icontains=search)
                | models.Q(legal_name__icontains=search),
            )
        return organisations

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["filter_query"] = self.filter_query()
        context["is_filtered"] = bool(
            self.filter_form.chosen("status") or self.filter_form.chosen("q"),
        )
        return context

    def filter_query(self) -> str:
        """The current filters as a query string, for the pagination links."""
        params = self.filter_form.data.copy()
        params.pop("page", None)
        return params.urlencode()

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        # A boosted nav click is also an htmx request, and it wants the whole
        # page; only the filter form asks for the results on their own.
        if request.htmx and not request.htmx.boosted:
            response.template_name = self.partial_template_name
        return response


def get_organisation(slug: str) -> Organisation:
    """One vendor, with the roster already loaded."""
    return get_object_or_404(
        Organisation.objects.prefetch_related("memberships__user"),
        slug=slug,
    )


def register_context(
    organisation: Organisation,
    *,
    verification_form: VerificationForm | None = None,
) -> dict:
    """Context for the vendor screen, in the shape the partial expects.

    The full page and the verification POST render the same fragment, so a
    swapped-in decision and a reloaded page cannot differ.
    """
    if verification_form is None:
        verification_form = VerificationForm(
            initial={"status": organisation.verification_status},
        )
    return {
        "nav_section": "organisations",
        "organisation": organisation,
        "memberships": organisation.memberships.all(),
        "verification_form": verification_form,
    }


class OrganisationDetailView(OhcConsoleMixin, View):
    """One vendor: the profile they submitted, who works there, and the decision."""

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        organisation = get_organisation(kwargs["slug"])
        return render(
            request,
            "ohc/organisation_detail.html",
            register_context(organisation),
        )


class OrganisationVerificationView(OhcConsoleMixin, View):
    """Verify a vendor, reject it, or put it back to pending.

    Answers htmx with the register fragment — the badge and the rail move
    together — and everyone else with POST → redirect → flash, which is the
    path that has to keep working with scripting off.
    """

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        organisation = get_organisation(kwargs["slug"])
        form = VerificationForm(request.POST)
        if not form.is_valid():
            # A rejected submission: 200 with the errors, never a redirect.
            return self.render_register(request, organisation, verification_form=form)

        status = form.cleaned_data["status"]
        # set_verification owns what each state means for verified_at, and
        # reports whether anything actually moved.
        if organisation.set_verification(status):
            messages.success(request, self.confirmation(organisation, status))
        else:
            messages.info(request, _("That vendor was already in that state."))

        if request.htmx:
            return self.render_register(request, organisation)
        return redirect("ohc:organisation", slug=organisation.slug)

    @staticmethod
    def confirmation(organisation: Organisation, status: str) -> str:
        """What the decision means, said in the vendor's own name.

        The queue and this screen both call a vendor by organisation.name, and
        a flash that switched to the legal entity would read as a different
        company entirely.
        """
        notes = {
            Organisation.VerificationStatus.VERIFIED: _(
                "%(vendor)s is now a verified vendor.",
            ),
            Organisation.VerificationStatus.REJECTED: _(
                "%(vendor)s is marked as rejected.",
            ),
            Organisation.VerificationStatus.PENDING: _(
                "%(vendor)s is back to pending verification.",
            ),
        }
        return notes[status] % {"vendor": organisation.name}

    @staticmethod
    def render_register(
        request: HttpRequest,
        organisation: Organisation,
        **forms,
    ) -> HttpResponse:
        context = register_context(organisation, **forms)
        if request.htmx:
            return render(
                request,
                "ohc/partials/organisation_register_swap.html",
                context,
            )
        return render(request, "ohc/organisation_detail.html", context)


# ── Events ─────────────────────────────────────────────────────────────────


class EventListView(OhcConsoleMixin, ListView):
    """Every event, draft and published — the console is where drafts exist."""

    template_name = "ohc/event_list.html"
    context_object_name = "events"
    paginate_by = QUEUE_PAGE_SIZE
    nav_section = "events"

    def get_queryset(self):
        # Newest first: the console is usually looking at what is next, or at
        # the draft written five minutes ago.
        return Event.objects.select_related("created_by").order_by("-starts_at")


class EventFormMixin(OhcConsoleMixin):
    model = Event
    form_class = EventForm
    template_name = "ohc/event_form.html"
    # Not Event.get_absolute_url(): that is the vendor-facing page, and this
    # flow ends back in the console's list.
    success_url = reverse_lazy("ohc:events")
    nav_section = "events"


class EventCreateView(EventFormMixin, CreateView):
    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("New event")
        context["submit_label"] = _("Create event")
        return context

    def form_valid(self, form: EventForm) -> HttpResponse:
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(
            self.request,
            _("“%(title)s” saved as a draft. Publish it when it is ready.")
            % {"title": self.object.title},
        )
        return response


class EventUpdateView(EventFormMixin, UpdateView):
    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Edit event")
        context["submit_label"] = _("Save changes")
        return context

    def form_valid(self, form: EventForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(
            self.request,
            _("“%(title)s” updated.") % {"title": self.object.title},
        )
        return response


class EventPublishToggleView(OhcConsoleMixin, View):
    """Publish or unpublish, as one POST — publishing is a state change."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        event = get_object_or_404(Event, slug=kwargs["slug"])
        if event.is_published:
            event.unpublish()
            note = _("“%(title)s” is hidden from vendors again.")
        else:
            event.publish()
            note = _("“%(title)s” is live for every vendor.")
        event.save(update_fields=["published_at", "updated_at"])
        messages.success(request, note % {"title": event.title})
        if request.htmx:
            # Only this event's state moved, so only its row comes back.
            return render(request, "ohc/partials/event_swap.html", {"event": event})
        return redirect("ohc:events")


# ── Sandboxes ────────────────────────────────────────────────────────────────


class SandboxQueueView(OhcConsoleMixin, ListView):
    """Every vendor's sandbox requests in one list."""

    template_name = "ohc/sandboxes.html"
    context_object_name = "sandboxes"
    nav_section = "sandboxes"

    def get_queryset(self):
        return Sandbox.objects.select_related("organisation", "requested_by")


class SandboxProvisionView(OhcConsoleMixin, View):
    """Kick off (or retry) provisioning for a requested sandbox."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        sandbox = get_object_or_404(Sandbox, pk=kwargs["pk"])
        if sandbox.status in {Sandbox.Status.REQUESTED, Sandbox.Status.FAILED}:
            sandbox.provisioned_by = request.user
            sandbox.status = Sandbox.Status.PROVISIONING
            sandbox.error = ""
            sandbox.save(
                update_fields=["provisioned_by", "status", "error", "modified_at"],
            )
            provision_sandbox.delay(sandbox.pk)
            messages.success(
                request,
                _("Provisioning started for %(org)s.")
                % {"org": sandbox.organisation},
            )
        else:
            messages.info(
                request,
                _("This sandbox is already %(status)s.")
                % {"status": sandbox.get_status_display()},
            )
        return redirect("ohc:sandboxes")


class SandboxRevokeView(OhcConsoleMixin, View):
    """Revoke a sandbox: delete the facility/users in Care, then free the slot."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        sandbox = get_object_or_404(Sandbox, pk=kwargs["pk"])
        organisation = sandbox.organisation
        if sandbox.job_id:
            try:
                CarePluginClient().delete_sandbox(sandbox.job_id)
            except CarePluginError as exc:
                messages.error(
                    request,
                    _("Could not revoke in Care: %(error)s") % {"error": exc},
                )
                return redirect("ohc:sandboxes")
        sandbox.delete()
        messages.success(
            request,
            _("Sandbox revoked for %(org)s. They can request a new one.")
            % {"org": organisation},
        )
        return redirect("ohc:sandboxes")
