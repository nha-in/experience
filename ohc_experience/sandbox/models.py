from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .catalog import MILESTONES


class ProductWorkspace(models.Model):
    product = models.OneToOneField(
        "experiences.Product",
        on_delete=models.CASCADE,
        related_name="workspace",
    )
    sandbox_id = models.CharField(max_length=32, unique=True)
    solution_type = models.CharField(
        max_length=40,
        choices=[
            ("clinical_hmis", "Clinical HMIS"),
            ("eua", "EUA"),
            ("health_locker", "Health Locker"),
        ],
    )
    applied_milestones = models.JSONField(default=list)
    registration_status = models.CharField(
        max_length=24,
        default="pending",
        choices=[
            ("pending", "Pending registration"),
            ("registered", "Registered"),
            ("sent_back", "Sent back"),
        ],
    )
    registered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.sandbox_id} - {self.product.name}"

    def get_absolute_url(self):
        return reverse("sandbox:overview", args=[self.sandbox_id])


class Milestone(models.Model):
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    key = models.CharField(
        max_length=20,
        choices=[
            (key, f"{value.code}: {value.name}") for key, value in MILESTONES.items()
        ],
    )
    application = models.OneToOneField(
        "experiences.ApplicationInstance",
        on_delete=models.PROTECT,
        related_name="milestone",
    )
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "key"],
                name="sandbox_unique_product_milestone",
            ),
        ]

    def __str__(self):
        return f"{self.product.name}: {self.definition.code}"

    @property
    def definition(self):
        return MILESTONES[self.key]


class ReviewItem(models.Model):
    class Kind(models.TextChoices):
        ORGANISATION = "organisation_verification", "Organisation verification"
        PRODUCT = "product_registration", "Product registration"
        EXIT = "exit_request", "Exit request"

    class Status(models.TextChoices):
        DRAFT = "draft", "In progress"
        NEW = "new", "New"
        IN_REVIEW = "in_review", "Under review"
        QUERY = "query_raised", "Query raised"
        APPROVED = "approved", "Approved"
        SENT_BACK = "sent_back", "Sent back"

    kind = models.CharField(max_length=32, choices=Kind)
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="sandbox_reviews",
    )
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sandbox_reviews",
    )
    application = models.OneToOneField(
        "experiences.ApplicationInstance",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sandbox_review",
    )
    form = models.ForeignKey(
        "experiences.FormRecord",
        on_delete=models.PROTECT,
        related_name="sandbox_reviews",
    )
    selected_submission = models.ForeignKey(
        "experiences.FormSubmission",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sandbox_reviews",
    )
    status = models.CharField(
        max_length=24,
        choices=Status,
        default=Status.DRAFT,
        db_index=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_sandbox_reviews",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resubmission_count = models.PositiveIntegerField(default=0)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_sandbox_reviews",
    )
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["submitted_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation"],
                condition=Q(kind="organisation_verification"),
                name="sandbox_one_org_review",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(kind="product_registration"),
                name="sandbox_one_product_review",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="organisation_verification",
                        product__isnull=True,
                        application__isnull=True,
                    )
                    | Q(
                        kind__in=["product_registration", "exit_request"],
                        product__isnull=False,
                        application__isnull=False,
                    )
                ),
                name="sandbox_review_subject_required",
            ),
        ]

    def __str__(self):
        return f"{self.reference} - {self.title}"

    def get_absolute_url(self):
        return reverse("sandbox:review", args=[self.pk])

    @property
    def reference(self):
        return f"REV-{self.pk:05d}"

    @property
    def title(self):
        if self.kind == self.Kind.EXIT:
            return self.application.title
        return self.product.name if self.product_id else self.organisation.display_name

    @property
    def editable(self):
        return self.status in {self.Status.DRAFT, self.Status.SENT_BACK}

    @property
    def pending(self):
        return self.status in {
            self.Status.NEW,
            self.Status.IN_REVIEW,
            self.Status.QUERY,
        }

    @property
    def age(self):
        return (timezone.now() - self.submitted_at).days if self.submitted_at else 0


class ReviewQuery(models.Model):
    item = models.ForeignKey(
        ReviewItem,
        on_delete=models.PROTECT,
        related_name="queries",
    )
    submission = models.ForeignKey(
        "experiences.FormSubmission",
        on_delete=models.PROTECT,
    )
    field_key = models.CharField(max_length=100, default="form")
    question = models.TextField()
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sandbox_questions",
    )
    raised_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=12,
        choices=[("open", "Open"), ("answered", "Answered"), ("resolved", "Resolved")],
        default="open",
    )
    reply = models.TextField(blank=True)
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sandbox_replies",
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["raised_at", "pk"]

    def __str__(self):
        return f"{self.item.reference}: {self.field_key} ({self.status})"


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        message = "Audit events are append-only."
        raise ValidationError(message)

    def delete(self):
        message = "Audit events are append-only."
        raise ValidationError(message)


class AuditEvent(models.Model):
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="sandbox_events",
    )
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sandbox_events",
    )
    item = models.ForeignKey(
        ReviewItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="history",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.action} ({self.created_at})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            message = "Audit events are append-only."
            raise ValidationError(message)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        message = "Audit events are append-only."
        raise ValidationError(message)


class SandboxCredential(models.Model):
    product = models.OneToOneField(
        "experiences.Product",
        on_delete=models.PROTECT,
        related_name="sandbox_credential",
    )
    client_id = models.CharField(max_length=100, unique=True)
    encrypted_secret = models.TextField(editable=False)
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("revoked", "Revoked")],
        default="active",
    )
    gateway_url = models.URLField()
    callback_url = models.URLField(blank=True)
    bridge_url = models.URLField(blank=True)
    issued_at = models.DateTimeField(default=timezone.now)
    rotation_due = models.DateTimeField()
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_status = models.PositiveSmallIntegerField(null=True, blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.client_id} ({self.status})"


class Notification(models.Model):
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.subject} to {self.recipient}"


class EventRegistration(models.Model):
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="registrations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_registrations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reminder_sent = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user"],
                name="sandbox_unique_event_registration",
            ),
        ]

    def __str__(self):
        return f"{self.event.title}: {self.user.email}"


class TicketContext(models.Model):
    ticket = models.OneToOneField(
        "support.Ticket",
        on_delete=models.CASCADE,
        related_name="sandbox_context",
    )
    product = models.ForeignKey("experiences.Product", on_delete=models.PROTECT)
    track = models.CharField(
        max_length=32,
        choices=[
            ("", "General"),
            ("HI-CM", "HI-CM"),
            ("UHI", "UHI"),
            ("NHCX", "NHCX"),
            ("PHR", "PHR"),
            ("HealthLocker", "HealthLocker"),
        ],
        blank=True,
    )

    def __str__(self):
        return f"{self.ticket.reference}: {self.product.name}"


class TicketAttachment(models.Model):
    message = models.ForeignKey(
        "support.TicketMessage",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to="sandbox-support/%Y/%m/")
    original_name = models.CharField(max_length=255)

    def __str__(self):
        return self.original_name
