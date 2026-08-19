"""Forms for the vendor's support screens."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Category
from .models import Priority
from .models import Status
from .models import Ticket

# The two moves a vendor may make on their own ticket. Closing is the Care
# team's call — a resolved ticket they disagree with can be reopened, which is
# why reopening lives here and closing does not.
VENDOR_STATUS_CHOICES = [
    (Status.RESOLVED, Status.RESOLVED.label),
    (Status.OPEN, Status.OPEN.label),
]


class TicketFilterForm(forms.Form):
    """The inbox's three pickers, read from the querystring.

    Everything is optional and the blank choice is the "all" option, so an
    untouched form filters nothing and the plain GET submit round-trips.
    """

    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=[("", _("All statuses")), *Status.choices],
    )
    category = forms.ChoiceField(
        label=_("Category"),
        required=False,
        choices=[("", _("All categories")), *Category.choices],
    )
    priority = forms.ChoiceField(
        label=_("Priority"),
        required=False,
        choices=[("", _("All priorities")), *Priority.choices],
    )

    def selected(self) -> dict[str, str]:
        """The filters actually in force, with the blanks dropped.

        A typed-in value that is not a choice leaves the form invalid; that
        reads as "no filter" rather than an error page, because the querystring
        is part of the URL a person can edit.
        """
        if not self.is_valid():
            return {}
        return {name: value for name, value in self.cleaned_data.items() if value}


class TicketCreateForm(forms.ModelForm):
    """Open a ticket: the subject line and the first message, in one form."""

    body = forms.CharField(
        label=_("What is happening?"),
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_(
            "Include the facility, the API call and anything you already ruled out.",
        ),
    )

    class Meta:
        model = Ticket
        fields = ["subject", "category", "priority", "linked_facility"]
        labels = {
            "linked_facility": _("Linked facility"),
        }
        help_texts = {
            "linked_facility": _("The sandbox or production facility this concerns."),
        }

    field_order = ["subject", "category", "priority", "linked_facility", "body"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].widget.attrs.setdefault(
            "placeholder",
            _("Webhook events not firing on demo facility"),
        )
        self.order_fields(self.field_order)


class TicketReplyForm(forms.Form):
    """One reply in a thread."""

    body = forms.CharField(
        label=_("Reply"),
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": _("Write your reply…")},
        ),
        error_messages={"required": _("Write something before sending a reply.")},
    )


class TicketStatusForm(forms.Form):
    """A vendor's status move, and the guard on which moves exist.

    The choices are the whole permission check: anything else — CLOSED most of
    all — never validates, so no view can move a ticket somewhere the vendor is
    not entitled to put it.
    """

    status = forms.ChoiceField(choices=VENDOR_STATUS_CHOICES)
