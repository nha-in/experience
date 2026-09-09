from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ohc_experience.abdm.tracks import TRACK_CHOICES

# Reference numbers start here so the first ticket does not read as TKT-1.
REFERENCE_SEED = 2000
REFERENCE_PREFIX = "TKT"


class Category(models.TextChoices):
    SANDBOX = "sandbox", _("Sandbox and credentials")
    API = "api", _("Gateway APIs")
    CERTIFICATION = "certification", _("Milestones and exit")
    DEPLOYMENT = "deployment", _("Production onboarding")
    BILLING = "billing", _("Account and organisation")


class Priority(models.TextChoices):
    HIGH = "high", _("High")
    MEDIUM = "medium", _("Medium")
    LOW = "low", _("Low")


class Status(models.TextChoices):
    """The four states from the support inbox screen.

    AWAITING_VENDOR is written from the vendor's point of view ("Awaiting your
    reply"); the OHC console relabels it, because on the queue side the same
    state means the ball is in the vendor's court.
    """

    OPEN = "open", _("Open")
    AWAITING_VENDOR = "awaiting_vendor", _("Awaiting your reply")
    RESOLVED = "resolved", _("Resolved")
    CLOSED = "closed", _("Closed")

    @classmethod
    def active(cls) -> list[str]:
        return [cls.OPEN, cls.AWAITING_VENDOR]


# Badge variant per status, so the vendor inbox and the OHC queue never drift.
STATUS_VARIANTS = {
    Status.OPEN: "info",
    Status.AWAITING_VENDOR: "warning",
    Status.RESOLVED: "success",
    Status.CLOSED: "neutral",
}
PRIORITY_VARIANTS = {
    Priority.HIGH: "destructive",
    Priority.MEDIUM: "warning",
    Priority.LOW: "neutral",
}

# First-response commitment per severity, in business days.
SLA_BUSINESS_DAYS = {
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 5,
}


class TicketQuerySet(models.QuerySet["Ticket"]):
    def for_organisation(self, organisation) -> TicketQuerySet:
        return self.filter(organisation=organisation)

    def open_only(self) -> TicketQuerySet:
        return self.filter(status__in=Status.active())

    def awaiting_ohc(self) -> TicketQuerySet:
        """Tickets whose last word came from the vendor — the queue's real work."""
        return self.filter(status=Status.OPEN)

    def with_related(self) -> TicketQuerySet:
        return self.select_related("organisation", "created_by", "assignee")


class Ticket(models.Model):
    """A support conversation between one vendor organisation and the Care team."""

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
    subject = models.CharField(_("Subject"), max_length=255)
    category = models.CharField(
        _("Category"),
        max_length=20,
        choices=Category,
        default=Category.SANDBOX,
    )
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
        # Only OHC staff answer tickets, so the picker never offers a vendor.
        limit_choices_to={"is_ohc_team": True},
    )
    linked_facility = models.CharField(
        _("Linked facility"),
        max_length=255,
        blank=True,
    )
    # Portal context: which product and track the problem is about.
    product = models.ForeignKey(
        "abdm.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        verbose_name=_("Product"),
    )
    track = models.CharField(
        _("Track"),
        max_length=20,
        choices=TRACK_CHOICES,
        blank=True,
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
        return reverse("support:detail", kwargs={"reference": self.reference})

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
    def is_open(self) -> bool:
        return self.status in Status.active()

    @property
    def queue_status_label(self) -> str:
        """The same state, read from the Care team's side of the conversation."""
        if self.status == Status.AWAITING_VENDOR:
            return _("Awaiting vendor")
        if self.status == Status.OPEN:
            return _("Needs a reply")
        return self.get_status_display()

    @property
    def sla_business_days(self) -> int:
        return SLA_BUSINESS_DAYS.get(self.priority, SLA_BUSINESS_DAYS[Priority.LOW])

    @property
    def sla_label(self) -> str:
        days = self.sla_business_days
        if days == 1:
            return str(_("First response within 1 business day"))
        return str(_("First response within %(days)s business days") % {"days": days})

    @property
    def sla_met(self) -> bool | None:
        """True/False once answered, None while the first reply is pending."""
        if self.first_responded_at is None:
            return None
        elapsed = self.first_responded_at - self.created_at
        return elapsed.days <= self.sla_business_days

    @property
    def context_label(self) -> str:
        parts = []
        if self.product_id:
            parts.append(self.product.name)
        if self.track:
            parts.append(self.track)
        return " · ".join(parts)


class TicketMessage(models.Model):
    """One entry in a ticket thread: a reply, or a recorded status change."""

    class Kind(models.TextChoices):
        REPLY = "reply", _("Reply")
        EVENT = "event", _("Status change")

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
    # leaves the OHC team.
    from_ohc_team = models.BooleanField(default=False, editable=False)
    attachment = models.FileField(
        _("Attachment"),
        upload_to="support/attachments/%Y/%m/",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Ticket message")
        verbose_name_plural = _("Ticket messages")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.ticket.reference} · {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_event(self) -> bool:
        return self.kind == self.Kind.EVENT

    @property
    def author_label(self) -> str:
        if self.author is None:
            return str(_("Removed user"))
        return self.author.name or self.author.email

    @property
    def attachment_name(self) -> str:
        return self.attachment.name.rsplit("/", 1)[-1] if self.attachment else ""


def post_reply(  # noqa: PLR0913
    ticket: Ticket,
    author,
    body: str,
    *,
    from_ohc_team: bool,
    attachment=None,
    notify: bool = True,
) -> TicketMessage:
    """Add a reply and move the ticket to the other party's court.

    A vendor reply reopens the ticket; an OHC reply puts it on the vendor. This
    lives here rather than in a view so the vendor inbox, the OHC console and
    the admin all move a ticket the same way. The other party is emailed
    unless `notify` is off (seeding).
    """
    message = TicketMessage.objects.create(
        ticket=ticket,
        author=author,
        body=body,
        kind=TicketMessage.Kind.REPLY,
        from_ohc_team=from_ohc_team,
        attachment=attachment,
    )
    updates = ["status", "updated_at"]
    ticket.status = Status.AWAITING_VENDOR if from_ohc_team else Status.OPEN
    if from_ohc_team and ticket.first_responded_at is None:
        ticket.first_responded_at = timezone.now()
        updates.append("first_responded_at")
    ticket.save(update_fields=updates)
    if notify:
        from .notifications import notify_reply  # noqa: PLC0415 - avoids a cycle

        notify_reply(message)
    return message


def record_status_change(ticket: Ticket, author, status: str) -> TicketMessage:
    """Move a ticket and leave a trace of it in the thread."""
    ticket.status = status
    updates = ["status", "updated_at"]
    if status == Status.RESOLVED and ticket.resolved_at is None:
        ticket.resolved_at = timezone.now()
        updates.append("resolved_at")
    ticket.save(update_fields=updates)
    label = Status(status).label
    return TicketMessage.objects.create(
        ticket=ticket,
        author=author,
        kind=TicketMessage.Kind.EVENT,
        body=str(label),
        from_ohc_team=bool(getattr(author, "is_ohc_team", False)),
    )
