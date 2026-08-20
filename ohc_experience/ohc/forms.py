"""Forms for the OHC console.

The queue's filters, the levers on one ticket, the organisation list's filters,
the verification decision, and the event editor. None of them writes state —
the console's views hand every change to the model that owns what it means:
support.models.post_reply / record_status_change for a ticket,
Organisation.set_verification for a vendor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from ohc_experience.events.models import Event
from ohc_experience.organisations.models import Organisation
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from ohc_experience.users.models import User

# The two assignee filters that are not a person. They share a field with real
# user ids, so they are spelled out here rather than guessed at in a template.
ASSIGNEE_UNASSIGNED = "unassigned"
ASSIGNEE_MINE = "mine"


def ohc_team_members() -> QuerySet[User]:
    """Everyone who can own a ticket, in the order every picker lists them."""
    return get_user_model().objects.filter(is_ohc_team=True).order_by("name", "email")


def queue_status_choices(*, include_any: bool = False) -> list[tuple[str, str]]:
    """Status choices worded from the Care team's side of the conversation.

    Read off unsaved Ticket instances rather than restated here, so a picker can
    never drift from the `queue_status_label` shown in the table beside it —
    "Awaiting your reply" is the vendor's wording and would be a lie in here.
    """
    choices = [
        (value, Ticket(status=value).queue_status_label) for value in Status.values
    ]
    if include_any:
        return [("", _("All statuses")), *choices]
    return choices


class TeamMemberChoiceField(forms.ModelChoiceField):
    """An OHC member picker that reads like the rest of the console.

    Without this the options fall back to User.__str__, which is the email
    address — and the queue's assignee filter beside it lists people by name.
    """

    def label_from_instance(self, obj: User) -> str:
        return obj.display_name


class GetFilterForm(forms.Form):
    """Base for the console's list filters, which arrive in the query string.

    Nothing is required and nothing is trusted: these screens are reached by
    hand-edited and shared URLs, so an unknown value has to degrade to "no
    filter" rather than to an error page.
    """

    def chosen(self, name: str) -> str:
        """The validated value of one filter, or "" when it did not validate.

        `is_valid()` is called for its side effect: it leaves `cleaned_data`
        holding every field that passed, so one bad parameter cannot take the
        other filters down with it.
        """
        self.is_valid()
        return self.cleaned_data.get(name) or ""


class TicketFilterForm(GetFilterForm):
    """The queue's three GET filters."""

    status = forms.ChoiceField(label=_("Status"), required=False)
    priority = forms.ChoiceField(label=_("Priority"), required=False)
    assignee = forms.ChoiceField(label=_("Assignee"), required=False)

    def __init__(self, *args, team: QuerySet[User], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = queue_status_choices(include_any=True)
        self.fields["priority"].choices = [
            ("", _("All priorities")),
            *Priority.choices,
        ]
        self.fields["assignee"].choices = [
            ("", _("Anyone")),
            (ASSIGNEE_UNASSIGNED, _("Unassigned")),
            (ASSIGNEE_MINE, _("Mine")),
            *[(str(member.pk), member.display_name) for member in team],
        ]


class TicketControlForm(forms.Form):
    """Status, priority and assignee — the console's three levers on a ticket.

    Deliberately not a ModelForm: a ModelForm would write the new status onto
    the instance behind the view's back, and moving a ticket has to go through
    record_status_change() so the thread records that it happened.
    """

    status = forms.ChoiceField(label=_("Status"))
    priority = forms.ChoiceField(label=_("Priority"), choices=Priority.choices)
    assignee = TeamMemberChoiceField(
        label=_("Assignee"),
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label=_("Unassigned"),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # All four states, Closed included: the console is the only place a
        # ticket can be closed.
        self.fields["status"].choices = queue_status_choices()
        self.fields["assignee"].queryset = ohc_team_members()


class TicketReplyForm(forms.Form):
    """One field, because a reply is one thing: the words."""

    body = forms.CharField(
        label=_("Reply to the vendor"),
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": _("Write your reply…"),
            },
        ),
    )


class OrganisationFilterForm(GetFilterForm):
    """The organisation list's two GET filters: verification state and a search.

    The list is both a directory and a triage screen, so it does not default to
    a status the way the queue does — "everyone, undecided first" answers both
    questions, and the ordering does the triage without hiding anybody.
    """

    status = forms.ChoiceField(
        label=_("Verification"),
        required=False,
        choices=[
            ("", _("All vendors")),
            *Organisation.VerificationStatus.choices,
        ],
    )
    q = forms.CharField(
        label=_("Search"),
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": _("Vendor name…")}),
    )


class VerificationForm(forms.Form):
    """Where a vendor's verification stands — the console's one lever on it.

    Not a ModelForm, for the same reason TicketControlForm is not: the write has
    to go through Organisation.set_verification(), which owns what each state
    means for `verified_at`.
    """

    status = forms.ChoiceField(
        label=_("Verification status"),
        choices=Organisation.VerificationStatus.choices,
    )


class EventForm(forms.ModelForm):
    """Create and edit an event.

    `slug`, `published_at` and `created_by` are not offered: the slug is derived
    on save, publishing is its own POST, and the author is the person signed in.
    """

    class Meta:
        model = Event
        fields = [
            "title",
            "kind",
            "summary",
            "description",
            "starts_at",
            "ends_at",
            "location",
            "join_url",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            # type="datetime-local" hands the job to the platform's own picker.
            # The format has to match what that input emits and expects, or an
            # existing value comes back blank in the field.
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def clean(self) -> dict:
        cleaned = super().clean()
        starts_at = cleaned.get("starts_at")
        ends_at = cleaned.get("ends_at")
        # Event has a CheckConstraint saying the same thing; catching it here
        # turns a database error into a message beside the field.
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", _("The end time has to be after the start."))
        return cleaned
