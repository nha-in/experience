"""The vendor's half of support — screens 1g (inbox) and 1h (thread).

Every view here is scoped by OrganisationMixin: the queryset starts from
`Ticket.objects.for_organisation(...)`, so a reference belonging to another
vendor simply is not in it and resolves as a 404. That is deliberate — a 403
would confirm the ticket exists, and a reference is guessable.

State never moves in this module. `post_reply()` and `record_status_change()`
in the model layer own that, so the inbox, the OHC console and the admin all
move a ticket the same way.

htmx is layered on top and nothing depends on it: each action keeps a real
`action=` and answers a plain POST with a redirect back to the thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView
from django.views.generic import DetailView
from django.views.generic import ListView

from ohc_experience.abdm.context_processors import pick_current_product
from ohc_experience.abdm.models import Product
from ohc_experience.organisations.views import OrganisationMixin

from .forms import TicketCreateForm
from .forms import TicketFilterForm
from .forms import TicketReplyForm
from .forms import TicketStatusForm
from .knowledge import related_knowledge
from .models import Status
from .models import Ticket
from .models import post_reply
from .models import record_status_change
from .selectors import filter_tickets
from .selectors import format_response_time
from .selectors import median_first_response
from .selectors import thread_messages
from .selectors import tickets_for
from .selectors import with_avatar

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

    from .models import TicketMessage

TICKETS_PER_PAGE = 25


# How many of the organisation's other tickets the thread's rail lists.
OTHER_TICKETS = 4


class SupportMixin(OrganisationMixin):
    """Vendor scoping plus the sidebar section, for every support screen."""

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "support"
        return context

    def get_ticket(self, reference: str) -> Ticket:
        """This vendor's ticket with that reference, or 404.

        The organisation scope is inside the lookup rather than checked after
        it, so there is no path to a Ticket object that belongs elsewhere.
        """
        return get_object_or_404(tickets_for(self.organisation), reference=reference)


class TicketListView(SupportMixin, ListView):
    """Screen 1g — the support inbox.

    The filters are an ordinary `<form method="get">` with a submit button, so
    they work with scripting off. hx-get on the same form refreshes just the
    results when a picker changes; the URL it pushes is the URL the plain form
    would have produced, so a reload and a swap show the same page.
    """

    template_name = "support/ticket_list.html"
    context_object_name = "tickets"
    paginate_by = TICKETS_PER_PAGE
    # The page includes this fragment; htmx swaps the same file back in.
    partial_template_name = "support/partials/ticket_results_swap.html"

    def get_queryset(self):
        self.filter_form = TicketFilterForm(self.request.GET)
        self.filters = self.filter_form.selected()
        return filter_tickets(tickets_for(self.organisation), **self.filters)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        median = median_first_response(self.organisation)
        context.update(
            {
                "filter_form": self.filter_form,
                "has_filters": bool(self.filters),
                # Everything except `page`, so Previous/Next carry the filters.
                "filter_querystring": urlencode(self.filters),
                "median_first_response": (
                    format_response_time(median) if median is not None else None
                ),
            },
        )
        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        # A boosted nav click is an htmx request too, and it wants the whole
        # page: the shell keeps #main-content out of it and swaps the nav from
        # the same response. Only the filter form and the pager ask for the
        # results on their own.
        if request.htmx and not request.htmx.boosted:
            # Same context, smaller template: the results card, plus the count
            # line out of band because it sits up in the filter row.
            return render(request, self.partial_template_name, response.context_data)
        return response


class TicketCreateView(SupportMixin, CreateView):
    """The new-ticket form.

    One fragment is live: the Track picker follows the product picker, so a
    change there asks this same URL, with `product` and `track` in the query
    string, for the picker redrawn with that product's tracks. The rest is a
    plain page.
    """

    model = Ticket
    form_class = TicketCreateForm
    template_name = "support/ticket_form.html"
    # The page includes this fragment; htmx swaps the same file back in.
    partial_template_name = "support/partials/track_field.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # A boosted nav click is an htmx request too, and wants the whole
        # page; only the product picker asks for the Track picker alone.
        if request.htmx and not request.htmx.boosted:
            self.object = None
            return render(
                request,
                self.partial_template_name,
                {"form": self.get_form()},
            )
        return super().get(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        # The product picker lists this organisation's products only.
        kwargs["organisation"] = self.organisation
        return kwargs

    def get_initial(self) -> dict:
        initial = super().get_initial()
        query = self.request.GET
        if "product" in query:
            # The picker's own choice. Blank is "Not product-specific", which
            # must not fall back to the sidebar's product.
            if query["product"]:
                initial["product"] = query["product"]
        else:
            # A fresh form: a ticket is most often about the product the
            # sidebar points at.
            product = pick_current_product(
                self.request,
                list(Product.objects.for_organisation(self.organisation)),
            )
            if product is not None:
                initial["product"] = product.pk
        if "track" in query:
            initial["track"] = query["track"]
        return initial

    def form_valid(self, form: TicketCreateForm):
        ticket = form.save(commit=False)
        ticket.organisation = self.organisation
        ticket.created_by = self.request.user
        ticket.save()
        # The opening message is a vendor reply like any other, so it goes
        # through the model layer rather than creating a row by hand.
        post_reply(
            ticket,
            self.request.user,
            form.cleaned_data["body"],
            from_ohc_team=False,
            attachment=form.cleaned_data.get("attachment"),
        )
        self.object = ticket
        messages.success(
            self.request,
            _("Ticket %(reference)s is open. We will reply here.")
            % {"reference": ticket.reference},
        )
        return redirect(ticket)


class TicketDetailView(SupportMixin, DetailView):
    """Screen 1h — the thread."""

    template_name = "support/ticket_detail.html"
    context_object_name = "ticket"
    slug_field = "reference"
    slug_url_kwarg = "reference"

    def get_queryset(self):
        return tickets_for(self.organisation)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["thread"] = thread_messages(self.object)
        context["reply_form"] = kwargs.get("reply_form") or TicketReplyForm()
        context["related_knowledge"] = related_knowledge(self.object.category)
        context["other_tickets"] = tickets_for(self.organisation).exclude(
            pk=self.object.pk,
        )[:OTHER_TICKETS]
        return context


class ThreadActionView(SupportMixin, View):
    """Base for the thread's two POST endpoints.

    Both answer htmx with the same fragment — the new entry for the end of the
    thread, and the pieces of the page it invalidates out of band — and answer
    everyone else the way they always were: POST, redirect, flash.
    """

    # The response to an htmx action: one thread entry plus the OOB swaps.
    update_template_name = "support/partials/thread_update.html"

    def thread_update(
        self,
        request: HttpRequest,
        ticket: Ticket,
        message: TicketMessage,
    ) -> HttpResponse:
        return render(
            request,
            self.update_template_name,
            {
                "ticket": ticket,
                "message": with_avatar(message),
                "reply_form": TicketReplyForm(),
                "nav_section": "support",
            },
        )


class TicketReplyView(ThreadActionView):
    """Post a reply. `post_reply` is what moves the ticket back to OPEN."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ticket = self.get_ticket(kwargs["reference"])
        form = TicketReplyForm(request.POST, request.FILES)
        if not form.is_valid():
            if request.htmx:
                # The only element in the response carries hx-swap-oob, so the
                # errored form replaces itself and nothing is appended to the
                # thread — which is what the target would otherwise receive.
                return render(
                    request,
                    "support/partials/reply_panel.html",
                    {"ticket": ticket, "reply_form": form, "oob": True},
                )
            messages.error(request, _("Write something before sending a reply."))
            return redirect(ticket)
        message = post_reply(
            ticket,
            request.user,
            form.cleaned_data["body"],
            from_ohc_team=False,
            attachment=form.cleaned_data.get("attachment"),
        )
        messages.success(request, _("Your reply was added to the ticket."))
        if request.htmx:
            return self.thread_update(request, ticket, message)
        return redirect(ticket)


class TicketStatusView(ThreadActionView):
    """Resolve or reopen. `record_status_change` writes the thread entry.

    The form's choices are the guard: CLOSED belongs to the Care team, so a
    POST asking for it never validates and never reaches the model layer.
    """

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ticket = self.get_ticket(kwargs["reference"])
        form = TicketStatusForm(request.POST)
        if not form.is_valid():
            msg = _("You can resolve or reopen a ticket. Closing it is our call.")
            raise PermissionDenied(msg)
        status = form.cleaned_data["status"]
        message = record_status_change(ticket, request.user, status)
        messages.success(
            request,
            _("Ticket marked resolved.")
            if status == Status.RESOLVED
            else _("Ticket reopened."),
        )
        if request.htmx:
            return self.thread_update(request, ticket, message)
        return redirect(ticket)
