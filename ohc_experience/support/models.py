from __future__ import annotations

import logging
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.db.models import F
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .emails import notify_support

logger = logging.getLogger(__name__)

# Reference numbers start here so the first ticket does not read as TKT-1.
REFERENCE_SEED = 2000
REFERENCE_PREFIX = "TKT"
# Joins the two priorities a priority change records, as in "High → Low".
PRIORITY_CHANGE_SEPARATOR = " → "


class Priority(models.TextChoices):
    HIGH = "high", _("High")
    MEDIUM = "medium", _("Medium")
    LOW = "low", _("Low")


class Status(models.TextChoices):
    """The three states from the support inbox screen.

    OPEN and AWAITING_INTEGRATOR are written from the integrator's point of view
    ("With NHA team", "Awaiting your reply"); the NHA console relabels both from
    its own side ("Needs a reply", "Awaiting integrator").

    CLOSED is labelled "Resolved": it absorbed the old resolved state, and the
    stored value stayed "closed".
    """

    OPEN = "open", _("With NHA team")
    AWAITING_INTEGRATOR = "awaiting_integrator", _("Awaiting your reply")
    CLOSED = "closed", _("Resolved")

    @classmethod
    def active(cls) -> list[str]:
        return [cls.OPEN, cls.AWAITING_INTEGRATOR]


# Badge variant per status, so the integrator inbox and the OHC queue never drift.
STATUS_VARIANTS = {
    Status.OPEN: "info",
    Status.AWAITING_INTEGRATOR: "warning",
    Status.CLOSED: "neutral",
}
PRIORITY_VARIANTS = {
    Priority.HIGH: "destructive",
    Priority.MEDIUM: "warning",
    Priority.LOW: "neutral",
}


class TicketQuerySet(models.QuerySet["Ticket"]):
    def for_organisation(self, organisation) -> TicketQuerySet:
        return self.filter(organisation=organisation)

    def open_only(self) -> TicketQuerySet:
        return self.filter(status__in=Status.active())

    def with_related(self) -> TicketQuerySet:
        return self.select_related(
            "organisation",
            "product",
            "product__workspace",
            "created_by",
            "assignee",
        )


class Ticket(models.Model):
    """A support conversation about one integrator product, answered by the NHA team."""

    reference = models.CharField(
        _("Reference"),
        max_length=20,
        unique=True,
        editable=False,
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.CASCADE,
        related_name="tickets",
        verbose_name=_("Organisation"),
    )
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.PROTECT,
        related_name="tickets",
        verbose_name=_("Product"),
    )
    subject = models.CharField(_("Subject"), max_length=255)
    # Holds the program's support category, which is the same unit staff support
    # permissions are granted for. The catch-all has a code of its own ("others").
    category = models.CharField(_("Category"), max_length=100)
    # The category's sub-menu entry. It labels the ticket for triage; the
    # category above it is what decides who may read and answer the ticket.
    issue_type = models.CharField(_("Issue type"), max_length=100, blank=True)
    priority = models.CharField(
        _("Priority"),
        max_length=10,
        choices=Priority,
        default=Priority.MEDIUM,
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status,
        default=Status.OPEN,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="tickets_opened",
        verbose_name=_("Opened by"),
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets_assigned",
        verbose_name=_("Assignee"),
        # Only NHA staff answer tickets, so the picker never offers an integrator.
        limit_choices_to={"is_nha_team": True},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    first_responded_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects: ClassVar[TicketQuerySet] = TicketQuerySet.as_manager()

    class Meta:
        verbose_name = _("Ticket")
        verbose_name_plural = _("Tickets")
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["organisation", "-updated_at"]),
            models.Index(fields=["status", "-updated_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(resolved_at__isnull=True)
                | Q(resolved_at__gte=F("created_at")),
                name="ticket_resolved_after_created",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.subject}"

    def save(self, *args, **kwargs) -> None:
        if not self.reference:
            self.reference = self._next_reference()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("experiences:ticket", kwargs={"reference": self.reference})

    @staticmethod
    def _next_reference() -> str:
        """Sequential, human-quotable reference.

        Derived from the max existing number rather than the row count, so
        deleting a ticket can never hand its reference to a new one.
        """
        latest = (
            Ticket.objects.order_by("-id").values_list("reference", flat=True).first()
        )
        if latest and latest.startswith(f"{REFERENCE_PREFIX}-"):
            suffix = latest.split("-", 1)[1]
            if suffix.isdigit():
                return f"{REFERENCE_PREFIX}-{int(suffix) + 1}"
        return f"{REFERENCE_PREFIX}-{REFERENCE_SEED + 1}"

    @property
    def status_variant(self) -> str:
        return STATUS_VARIANTS.get(self.status, "neutral")

    @property
    def priority_variant(self) -> str:
        return PRIORITY_VARIANTS.get(self.priority, "neutral")

    @property
    def support_category(self):
        """The definition this ticket was filed under, or None once retired."""
        workspace = getattr(self.product, "workspace", None)
        if workspace is None:
            return None
        return workspace.definition.support_category_map().get(self.category)

    @property
    def category_label(self) -> str:
        """A category the program has since dropped still reads as it was filed."""
        category = self.support_category
        if category is not None:
            return category.name
        return self.category

    @property
    def is_open(self) -> bool:
        return self.status in Status.active()

    @property
    def integrator_status_label(self) -> str:
        return self.get_status_display()

    @property
    def queue_status_label(self) -> str:
        """The same state, read from the NHA team's side of the conversation."""
        if self.status == Status.AWAITING_INTEGRATOR:
            return _("Awaiting integrator")
        if self.status == Status.OPEN:
            return _("Needs a reply")
        return self.get_status_display()


class TicketMessage(models.Model):
    """One entry in a ticket thread: a reply, or a status or priority change."""

    class Kind(models.TextChoices):
        REPLY = "reply", _("Reply")
        EVENT = "event", _("Status change")
        PRIORITY = "priority", _("Priority change")

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name=_("Ticket"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ticket_messages",
        verbose_name=_("Author"),
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REPLY)
    body = models.TextField(_("Message"))
    # Denormalised so a reply still reads correctly if the author later joins or
    # leaves the NHA team.
    from_nha_team = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Ticket message")
        verbose_name_plural = _("Ticket messages")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        created = timezone.localtime(self.created_at)
        return f"{self.ticket.reference} · {created:%d/%m/%Y, %H:%M}"

    @property
    def is_event(self) -> bool:
        """A change the thread records, rather than something someone wrote."""
        return self.kind != self.Kind.REPLY

    @property
    def priority_change(self) -> tuple[str, str]:
        """The priority a priority entry replaced, and the one it set."""
        previous, _separator, current = self.body.partition(PRIORITY_CHANGE_SEPARATOR)
        return previous, current

    @property
    def author_label(self) -> str:
        if self.author is None:
            return str(_("Removed user"))
        return self.author.name or self.author.email


def post_reply(
    ticket: Ticket,
    author,
    body: str,
    *,
    from_nha_team: bool,
    resolve: bool = False,
) -> TicketMessage:
    """Add a reply and move the ticket to the other party's court.

    An integrator reply reopens the ticket; an NHA reply puts it on the
    integrator. Resolving closes it instead: the reply says how, and a Resolved
    entry follows it in the thread. This lives here rather than in a view so the
    integrator inbox, the NHA console and the admin all move a ticket the same
    way. Each reply is mirrored into the support email thread, once the ticket
    has moved, so a resolving reply goes out as a single email.
    """
    message = TicketMessage.objects.create(
        ticket=ticket,
        author=author,
        body=body,
        kind=TicketMessage.Kind.REPLY,
        from_nha_team=from_nha_team,
    )
    updates = ["status", "updated_at"]
    if from_nha_team and ticket.first_responded_at is None:
        ticket.first_responded_at = timezone.now()
        updates.append("first_responded_at")
    if resolve:
        ticket.status = Status.CLOSED
        if ticket.resolved_at is None:
            ticket.resolved_at = timezone.now()
            updates.append("resolved_at")
        TicketMessage.objects.create(
            ticket=ticket,
            author=author,
            kind=TicketMessage.Kind.EVENT,
            body=str(Status.CLOSED.label),
            from_nha_team=from_nha_team,
        )
    else:
        ticket.status = Status.AWAITING_INTEGRATOR if from_nha_team else Status.OPEN
    ticket.save(update_fields=updates)
    _mirror_to_support(ticket, message)
    return message


def change_priority(ticket: Ticket, author, priority: str) -> TicketMessage | None:
    """Set a ticket's priority, and record in the thread what it replaced.

    The NHA team corrects a priority an integrator raised without cause, or one
    filed too low. The entry tells the integrator who changed it, and keeps the
    priority they filed with in view afterwards. Choosing the current priority
    again changes nothing. The support email thread mirrors replies only, so
    nothing is sent.
    """
    if priority not in Priority.values:
        msg = _("Choose High, Medium or Low.")
        raise ValidationError(msg)
    with transaction.atomic():
        # Read under a lock, so two changes at once each record what they replaced.
        previous = (
            Ticket.objects.select_for_update()
            .values_list("priority", flat=True)
            .get(pk=ticket.pk)
        )
        ticket.priority = priority
        if priority == previous:
            return None
        ticket.save(update_fields=["priority", "updated_at"])
        return TicketMessage.objects.create(
            ticket=ticket,
            author=author,
            kind=TicketMessage.Kind.PRIORITY,
            body=PRIORITY_CHANGE_SEPARATOR.join(
                str(Priority(value).label) for value in (previous, priority)
            ),
            from_nha_team=True,
        )


def _mirror_to_support(ticket: Ticket, message: TicketMessage) -> None:
    try:
        notify_support(ticket, message)
    except Exception:
        # Email must never break the ticket flow.
        logger.exception("Failed to email support for %s", ticket.reference)
