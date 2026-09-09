"""Forms for the vendor's support screens."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from ohc_experience.abdm.models import Product
from ohc_experience.abdm.tracks import TRACK_CHOICES
from ohc_experience.abdm.uploads import DOCUMENTS
from ohc_experience.abdm.uploads import PDF_MAX_MB
from ohc_experience.abdm.uploads import validate_upload

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


ATTACHMENT_TYPES = DOCUMENTS | frozenset({".txt", ".log", ".json"})


class AttachmentMixin:
    """One optional file on a message: PDF, image, or a text/log/JSON dump."""

    def clean_attachment(self):
        upload = self.cleaned_data.get("attachment")
        validate_upload(upload, extensions=ATTACHMENT_TYPES, max_mb=PDF_MAX_MB)
        return upload


def track_choices(product) -> list[tuple[str, str]]:
    """The Track picker's options: the product's own tracks, or every track."""
    tracks = (
        TRACK_CHOICES
        if product is None
        else [(track.code, track.code) for track in product.tracks]
    )
    return [("", _("Not track-specific")), *tracks]


class TicketCreateForm(AttachmentMixin, forms.ModelForm):
    """Open a ticket: the subject line and the first message, in one form.

    The Track picker follows the product: with one chosen it offers only the
    tracks that product applied for, and refuses any other on submit. Without
    one it offers every track. The view redraws the picker through htmx when
    the product changes; with scripting off the form validates the pair on
    submit either way.
    """

    body = forms.CharField(
        label=_("What is happening?"),
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_(
            "Include the request id, the API call and anything you already ruled out.",
        ),
    )
    track = forms.ChoiceField(
        label=_("Track"),
        required=False,
        choices=[("", _("Not track-specific")), *TRACK_CHOICES],
    )
    attachment = forms.FileField(
        label=_("Attachment"),
        required=False,
        help_text=_("PDF, image, text, log or JSON, up to 10 MB."),
    )

    class Meta:
        model = Ticket
        fields = [
            "subject",
            "category",
            "priority",
            "product",
            "track",
            "linked_facility",
        ]
        labels = {
            "priority": _("Severity"),
            "product": _("Product"),
            "linked_facility": _("Linked facility"),
        }
        help_texts = {
            "linked_facility": _(
                "The sandbox facility or HFR id this concerns, if any.",
            ),
        }

    field_order = [
        "subject",
        "category",
        "priority",
        "product",
        "track",
        "linked_facility",
        "body",
        "attachment",
    ]

    def __init__(self, *args, organisation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].widget.attrs.setdefault(
            "placeholder",
            _("Callback never received after a consent request"),
        )
        self.fields["product"].required = False
        self.fields["product"].empty_label = _("Not product-specific")
        self.fields["product"].queryset = (
            Product.objects.for_organisation(organisation)
            if organisation is not None
            else Product.objects.none()
        )
        self.fields["product"].label_from_instance = lambda product: (
            f"{product.name} ({product.sandbox_id})"
        )
        self.fields["linked_facility"].required = False
        product = self.chosen_product()
        self.fields["track"].choices = track_choices(product)
        if product is not None:
            self.fields["track"].error_messages["invalid_choice"] = _(
                "That product is not on this track. Pick one of its tracks, "
                "or leave it blank.",
            )
        offered = {value for value, _label in self.fields["track"].choices}
        if not self.is_bound and self.initial.get("track") not in offered:
            # A redraw for another product: a track it is not on is dropped
            # rather than drawn as a selection the picker cannot show.
            self.initial.pop("track", None)
        self.order_fields(self.field_order)

    def chosen_product(self):
        """The product the ticket is about: as posted, else as pre-filled."""
        value = (
            self.data.get("product") if self.is_bound else self.initial.get("product")
        )
        if isinstance(value, Product):
            return value
        try:
            return self.fields["product"].queryset.get(pk=int(value))
        except (TypeError, ValueError, Product.DoesNotExist):
            return None


class TicketReplyForm(AttachmentMixin, forms.Form):
    """One reply in a thread, with an optional file."""

    body = forms.CharField(
        label=_("Reply"),
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": _("Write your reply…")},
        ),
        error_messages={"required": _("Write something before sending a reply.")},
    )
    attachment = forms.FileField(
        label=_("Attachment"),
        required=False,
        help_text=_("PDF, image, text, log or JSON, up to 10 MB."),
    )


class TicketStatusForm(forms.Form):
    """A vendor's status move, and the guard on which moves exist.

    The choices are the whole permission check: anything else — CLOSED most of
    all — never validates, so no view can move a ticket somewhere the vendor is
    not entitled to put it.
    """

    status = forms.ChoiceField(choices=VENDOR_STATUS_CHOICES)
