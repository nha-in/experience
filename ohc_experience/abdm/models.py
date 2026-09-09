"""The portal's own rows: products, credentials, exit requests and reviews.

Status machines are documented in docs/superpowers/specs. Nothing here moves a
status on its own — ``services.py`` is the only writer — but each model knows
how to describe itself: a badge variant, a label, whether it is editable.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import tracks
from .crypto import decrypt_secret
from .crypto import encrypt_secret

WHOLE_FORM = "form"


class ProductQuerySet(models.QuerySet["Product"]):
    def for_organisation(self, organisation) -> ProductQuerySet:
        return self.filter(organisation=organisation)

    def with_related(self) -> ProductQuerySet:
        return self.select_related("organisation", "created_by").prefetch_related(
            "compliance_records",
        )


class Product(models.Model):
    """An integrator's product, identified everywhere by its sandbox id."""

    class Category(models.TextChoices):
        HMIS = "hmis", _("HMIS")
        LMIS = "lmis", _("LMIS")
        PHR_APP = "phr_app", _("PHR application")
        HEALTH_LOCKER = "health_locker", _("Health locker")
        PAYER_TPA = "payer_tpa", _("Payer or TPA system")
        OTHER = "other", _("Other")

    class SolutionType(models.TextChoices):
        CLINICAL_HMIS = "clinical_hmis", _("Clinical HMIS")
        EUA = "eua", _("EUA")
        HEALTH_LOCKER = "health_locker", _("Health Locker")

    class RegistrationStatus(models.TextChoices):
        PENDING = "pending", _("Registration pending")
        REGISTERED = "registered", _("Registered")
        SENT_BACK = "sent_back", _("Sent back")

    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name=_("Organisation"),
    )
    sandbox_id = models.CharField(_("Sandbox id"), max_length=20, unique=True)
    name = models.CharField(_("Product name"), max_length=255)
    description = models.TextField(_("Description"))
    # Both default to the common case, a clinical HMIS, so the registration
    # form opens on a sensible answer rather than a blank to pick through.
    category = models.CharField(
        _("Category"),
        max_length=20,
        choices=Category,
        default=Category.HMIS,
    )
    solution_type = models.CharField(
        _("Solution type applying for"),
        max_length=20,
        choices=SolutionType,
        default=SolutionType.CLINICAL_HMIS,
    )
    # Track codes and canonical milestone keys ("HI-CM:M1"), see tracks.py.
    applied_tracks = models.JSONField(_("Applied tracks"), default=list, blank=True)
    applied_milestones = models.JSONField(
        _("Applied milestones"),
        default=list,
        blank=True,
    )
    registration_status = models.CharField(
        _("Registration status"),
        max_length=20,
        choices=RegistrationStatus,
        default=RegistrationStatus.PENDING,
    )
    registered_on = models.DateField(_("Registered on"), null=True, blank=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registered_products",
    )
    sent_back_reason = models.TextField(_("Send-back reason"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_products",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ProductQuerySet] = ProductQuerySet.as_manager()

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.sandbox_id} · {self.name}"

    def get_absolute_url(self) -> str:
        return reverse("products:overview", kwargs={"sandbox_id": self.sandbox_id})

    @property
    def status_variant(self) -> str:
        return {
            self.RegistrationStatus.REGISTERED: "success",
            self.RegistrationStatus.SENT_BACK: "destructive",
        }.get(self.registration_status, "warning")

    @property
    def is_registered(self) -> bool:
        return self.registration_status == self.RegistrationStatus.REGISTERED

    @property
    def is_sent_back(self) -> bool:
        return self.registration_status == self.RegistrationStatus.SENT_BACK

    @property
    def tracks(self) -> list[tracks.Track]:
        """Applied tracks in sidebar order."""
        applied = set(self.applied_tracks or [])
        return [track for track in tracks.TRACKS if track.code in applied]

    def has_track(self, track_code: str) -> bool:
        return track_code in set(self.applied_tracks or [])

    def record_map(self) -> dict[str, ComplianceRecord]:
        """Every compliance record keyed by its canonical ``TRACK:CODE``."""
        return {record.key: record for record in self.compliance_records.all()}

    def records_for_track(self, track: tracks.Track) -> list[dict]:
        """One entry per milestone tile on the track, record included if applied."""
        record_map = self.record_map()
        return [
            {"milestone": milestone, "record": record_map.get(milestone.canonical_key)}
            for milestone in track.milestones
        ]

    def approved_count(self, track: tracks.Track) -> tuple[int, int]:
        """(approved, applied) milestones on a track."""
        entries = [
            entry["record"]
            for entry in self.records_for_track(track)
            if entry["record"] is not None
        ]
        approved = sum(
            record.status == ComplianceRecord.Status.APPROVED for record in entries
        )
        return approved, len(entries)

    @property
    def active_credential(self):
        credential = getattr(self, "credential", None)
        return credential if credential and credential.is_active else None


class Credential(models.Model):
    """Sandbox gateway credentials for one product. The secret is encrypted."""

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="credential",
    )
    client_id = models.CharField(_("Client id"), max_length=80, unique=True)
    secret_encrypted = models.TextField(_("Client secret"))
    gateway_base_url = models.URLField(_("Gateway base URL"))
    callback_url = models.URLField(_("Callback URL"), blank=True)
    bridge_url = models.URLField(_("Bridge URL"), blank=True)
    issued_on = models.DateTimeField(_("Issued on"))
    rotation_due = models.DateField(_("Rotation due"))
    rotated_on = models.DateTimeField(_("Last rotated"), null=True, blank=True)
    revoked_on = models.DateTimeField(_("Revoked on"), null=True, blank=True)
    callback_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    callback_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    callback_checked_at = models.DateTimeField(null=True, blank=True)
    callback_error = models.CharField(max_length=255, blank=True)
    callback_failure_streak = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Credential")
        verbose_name_plural = _("Credentials")

    def __str__(self) -> str:
        return self.client_id

    @property
    def secret(self) -> str:
        return decrypt_secret(self.secret_encrypted)

    def set_secret(self, value: str) -> None:
        self.secret_encrypted = encrypt_secret(value)

    @property
    def is_active(self) -> bool:
        return self.revoked_on is None

    @property
    def rotation_overdue(self) -> bool:
        return self.is_active and self.rotation_due < timezone.localdate()

    @property
    def callback_checked(self) -> bool:
        return self.callback_checked_at is not None

    @property
    def callback_ok(self) -> bool | None:
        if not self.callback_checked:
            return None
        code = self.callback_status_code
        return bool(code and HTTPStatusRange.OK_MIN <= code < HTTPStatusRange.OK_MAX)

    @property
    def callback_variant(self) -> str:
        ok = self.callback_ok
        if ok is None:
            return "neutral"
        return "success" if ok else "destructive"

    @property
    def callback_label(self) -> str:
        ok = self.callback_ok
        if ok is None:
            return str(_("Not checked yet"))
        return str(_("Reachable")) if ok else str(_("Unreachable"))


class HTTPStatusRange:
    OK_MIN = 200
    OK_MAX = 400


class ComplianceRecord(models.Model):
    """One exit request: a product's evidence for one milestone on one track."""

    class Status(models.TextChoices):
        LOCKED = "locked", _("Locked")
        OPEN = "open", _("Open")
        IN_PROGRESS = "in_progress", _("In progress")
        UNDER_REVIEW = "under_review", _("Under review")
        QUERY_RAISED = "query_raised", _("Query raised")
        APPROVED = "approved", _("Approved")

    EDITABLE_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {Status.OPEN, Status.IN_PROGRESS},
    )
    REVIEW_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {Status.UNDER_REVIEW, Status.QUERY_RAISED},
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="compliance_records",
    )
    track_code = models.CharField(_("Track"), max_length=20)
    milestone_code = models.CharField(_("Milestone"), max_length=20)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status,
        default=Status.LOCKED,
        db_index=True,
    )
    start_date = models.DateField(_("Sandbox testing start"), null=True, blank=True)
    end_date = models.DateField(_("Sandbox testing end"), null=True, blank=True)
    demo_date = models.DateField(_("Tentative demo date"), null=True, blank=True)
    wasa_agency = models.CharField(_("WASA audit agency"), max_length=255, blank=True)
    wasa_date = models.DateField(_("WASA date"), null=True, blank=True)
    functional_certificate = models.FileField(
        _("Functional testing certificate"),
        upload_to="abdm/compliance/%Y/%m/",
        blank=True,
    )
    functional_report = models.FileField(
        _("Functional testing report"),
        upload_to="abdm/compliance/%Y/%m/",
        blank=True,
    )
    submitted_on = models.DateTimeField(_("Submitted on"), null=True, blank=True)
    resubmission_count = models.PositiveSmallIntegerField(default=0)
    approved_on = models.DateField(_("Approved on"), null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_compliance_records",
    )
    decision_note = models.TextField(_("Decision note"), blank=True)
    sent_back_on = models.DateTimeField(_("Sent back on"), null=True, blank=True)
    sent_back_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_back_compliance_records",
    )
    sent_back_reason = models.TextField(_("Send-back reason"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Compliance record")
        verbose_name_plural = _("Compliance records")
        ordering = ["product", "track_code", "milestone_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "track_code", "milestone_code"],
                name="unique_compliance_record_per_milestone",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.sandbox_id} {self.key} ({self.status})"

    @property
    def key(self) -> str:
        return f"{self.track_code}:{self.milestone_code}"

    @property
    def track(self) -> tracks.Track:
        return tracks.get_track(self.track_code)

    @property
    def milestone(self) -> tracks.Milestone:
        return tracks.get_milestone(self.track_code, self.milestone_code)

    @property
    def label(self) -> str:
        return f"{self.track_code} {self.milestone.label}"

    @property
    def is_editable(self) -> bool:
        return self.status in self.EDITABLE_STATUSES

    @property
    def is_under_review(self) -> bool:
        return self.status in self.REVIEW_STATUSES

    @property
    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_locked(self) -> bool:
        return self.status == self.Status.LOCKED

    @property
    def was_sent_back(self) -> bool:
        """Sent back and not yet resubmitted — what the orange banner keys on."""
        return bool(self.sent_back_on) and self.is_editable

    @property
    def missing_fields(self) -> list[str]:
        required = [
            ("start_date", _("Sandbox testing start")),
            ("end_date", _("Sandbox testing end")),
            ("demo_date", _("Tentative demo date")),
            ("wasa_agency", _("WASA audit agency")),
            ("wasa_date", _("WASA date")),
            ("functional_certificate", _("Functional testing certificate")),
            ("functional_report", _("Functional testing report")),
        ]
        return [str(label) for name, label in required if not getattr(self, name)]

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    @property
    def status_variant(self) -> str:
        return {
            self.Status.LOCKED: "neutral",
            self.Status.OPEN: "neutral",
            self.Status.IN_PROGRESS: "warning",
            self.Status.UNDER_REVIEW: "info",
            self.Status.QUERY_RAISED: "warning",
            self.Status.APPROVED: "success",
        }.get(self.status, "neutral")

    @property
    def review_item(self):
        return getattr(self, "review", None)


class ReviewItemQuerySet(models.QuerySet["ReviewItem"]):
    def open(self) -> ReviewItemQuerySet:
        return self.filter(status__in=ReviewItem.OPEN_STATUSES)

    def decided(self) -> ReviewItemQuerySet:
        return self.filter(status__in=ReviewItem.DECIDED_STATUSES)

    def of_type(self, item_type: str) -> ReviewItemQuerySet:
        return self.filter(item_type=item_type)

    def with_related(self) -> ReviewItemQuerySet:
        return self.select_related(
            "organisation",
            "product",
            "compliance",
            "assignee",
            "decided_by",
        )


class ReviewItem(models.Model):
    """What a reviewer decides on: one subject, one queue row, one history."""

    class Type(models.TextChoices):
        EXIT_REQUEST = "exit_request", _("Exit request")
        ORGANISATION_VERIFICATION = (
            "organisation_verification",
            _("Organisation verification"),
        )
        PRODUCT_REGISTRATION = "product_registration", _("Product registration")

    class Status(models.TextChoices):
        NEW = "new", _("New")
        IN_REVIEW = "in_review", _("In review")
        QUERY_RAISED = "query_raised", _("Query raised")
        APPROVED = "approved", _("Approved")
        SENT_BACK = "sent_back", _("Sent back")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    OPEN_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {Status.NEW, Status.IN_REVIEW, Status.QUERY_RAISED},
    )
    DECIDED_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {Status.APPROVED, Status.SENT_BACK},
    )

    reference = models.CharField(_("Reference"), max_length=20, unique=True)
    item_type = models.CharField(_("Type"), max_length=40, choices=Type, db_index=True)
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.CASCADE,
        related_name="review_items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="review_items",
    )
    compliance = models.OneToOneField(
        ComplianceRecord,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="review",
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status,
        default=Status.NEW,
        db_index=True,
    )
    submitted_on = models.DateTimeField(_("Submitted on"))
    resubmission_count = models.PositiveSmallIntegerField(default=0)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_review_items",
        limit_choices_to={"is_ohc_team": True},
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_on = models.DateField(_("Decided on"), null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_review_items",
    )
    decision_note = models.TextField(_("Decision note"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ReviewItemQuerySet] = ReviewItemQuerySet.as_manager()

    class Meta:
        verbose_name = _("Review item")
        verbose_name_plural = _("Review items")
        ordering = ["submitted_on", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation"],
                condition=Q(item_type="organisation_verification"),
                name="unique_organisation_verification_item",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(item_type="product_registration"),
                name="unique_product_registration_item",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} · {self.title}"

    def get_absolute_url(self) -> str:
        return reverse("assess:review", kwargs={"reference": self.reference})

    @property
    def is_exit_request(self) -> bool:
        return self.item_type == self.Type.EXIT_REQUEST

    @property
    def is_organisation_verification(self) -> bool:
        return self.item_type == self.Type.ORGANISATION_VERIFICATION

    @property
    def is_product_registration(self) -> bool:
        return self.item_type == self.Type.PRODUCT_REGISTRATION

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def is_decided(self) -> bool:
        return self.status in self.DECIDED_STATUSES

    @property
    def track_code(self) -> str:
        return self.compliance.track_code if self.compliance_id else ""

    @property
    def milestone_code(self) -> str:
        return self.compliance.milestone_code if self.compliance_id else ""

    @property
    def title(self) -> str:
        if self.is_exit_request and self.compliance_id:
            return str(
                _("%(track)s %(milestone)s exit request")
                % {
                    "track": self.compliance.track_code,
                    "milestone": self.compliance.milestone_code,
                },
            )
        if self.is_product_registration:
            return str(_("Product registration"))
        return str(_("Organisation verification"))

    @property
    def subject_label(self) -> str:
        if self.product_id:
            return self.product.name
        return self.organisation.name

    @property
    def age_days(self) -> int:
        return (timezone.now() - self.submitted_on).days

    @property
    def needs_attention(self) -> bool:
        return self.is_open and self.age_days > settings.ABDM_REVIEW_ATTENTION_DAYS

    @property
    def status_variant(self) -> str:
        return {
            self.Status.NEW: "info",
            self.Status.IN_REVIEW: "primary",
            self.Status.QUERY_RAISED: "warning",
            self.Status.APPROVED: "success",
            self.Status.SENT_BACK: "destructive",
            self.Status.WITHDRAWN: "neutral",
        }.get(self.status, "neutral")

    @property
    def type_variant(self) -> str:
        return {
            self.Type.EXIT_REQUEST: "primary",
            self.Type.ORGANISATION_VERIFICATION: "info",
            self.Type.PRODUCT_REGISTRATION: "neutral",
        }.get(self.item_type, "neutral")

    @property
    def open_query_count(self) -> int:
        return self.queries.filter(status=ReviewQuery.Status.OPEN).count()

    @property
    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_sent_back(self) -> bool:
        return self.status == self.Status.SENT_BACK

    @property
    def is_withdrawn(self) -> bool:
        return self.status == self.Status.WITHDRAWN


class ReviewQuery(models.Model):
    """A reviewer's question against one field, or the whole form."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ANSWERED = "answered", _("Answered")
        RESOLVED = "resolved", _("Resolved")

    item = models.ForeignKey(
        ReviewItem,
        on_delete=models.CASCADE,
        related_name="queries",
    )
    field_key = models.CharField(_("Field"), max_length=80, default=WHOLE_FORM)
    field_label = models.CharField(_("Field label"), max_length=255, blank=True)
    question = models.TextField(_("Question"))
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="raised_review_queries",
    )
    raised_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status,
        default=Status.OPEN,
        db_index=True,
    )
    reply = models.TextField(_("Reply"), blank=True)
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answered_review_queries",
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_review_queries",
    )

    class Meta:
        verbose_name = _("Review query")
        verbose_name_plural = _("Review queries")
        ordering = ["raised_at", "pk"]

    def __str__(self) -> str:
        return f"{self.item.reference}: {self.question[:60]}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def is_answered(self) -> bool:
        return self.status == self.Status.ANSWERED

    @property
    def is_resolved(self) -> bool:
        return self.status == self.Status.RESOLVED

    @property
    def is_whole_form(self) -> bool:
        return self.field_key == WHOLE_FORM

    @property
    def against_label(self) -> str:
        if self.is_whole_form:
            return str(_("Whole form"))
        return self.field_label or self.field_key

    @property
    def status_variant(self) -> str:
        return {
            self.Status.OPEN: "warning",
            self.Status.ANSWERED: "info",
            self.Status.RESOLVED: "success",
        }.get(self.status, "neutral")


class ReviewHistory(models.Model):
    """Append-only record of everything that happened to a review item."""

    class Kind(models.TextChoices):
        SUBMITTED = "submitted", _("Submitted")
        RESUBMITTED = "resubmitted", _("Resubmitted")
        STARTED = "started", _("Review started")
        ASSIGNED = "assigned", _("Assigned")
        QUERY_RAISED = "query_raised", _("Query raised")
        QUERY_ANSWERED = "query_answered", _("Query answered")
        QUERY_RESOLVED = "query_resolved", _("Query resolved")
        APPROVED = "approved", _("Approved")
        SENT_BACK = "sent_back", _("Sent back")
        WITHDRAWN = "withdrawn", _("Withdrawn")
        MILESTONES_CHANGED = "milestones_changed", _("Milestones changed")
        NOTE = "note", _("Note")

    item = models.ForeignKey(
        ReviewItem,
        on_delete=models.CASCADE,
        related_name="history",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_history_entries",
    )
    kind = models.CharField(_("Kind"), max_length=30, choices=Kind)
    title = models.CharField(_("Title"), max_length=255)
    description = models.TextField(_("Description"), blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Review history entry")
        verbose_name_plural = _("Review history entries")
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.item.reference}: {self.title}"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            raise ValidationError(_("Review history cannot be changed."))
        super().save(*args, **kwargs)

    @property
    def actor_label(self) -> str:
        return self.actor.display_name if self.actor else str(_("System"))

    @property
    def variant(self) -> str:
        return {
            self.Kind.APPROVED: "success",
            self.Kind.SENT_BACK: "destructive",
            self.Kind.QUERY_RAISED: "warning",
            self.Kind.WITHDRAWN: "neutral",
        }.get(self.kind, "info")


class AuditLog(models.Model):
    """Append-only: actor, timestamp and diff for every service write."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    model_label = models.CharField(max_length=100, db_index=True)
    object_pk = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=255)
    action = models.CharField(max_length=60)
    diff = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Audit log entry")
        verbose_name_plural = _("Audit log entries")
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.action} {self.model_label}#{self.object_pk}"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            raise ValidationError(_("Audit entries cannot be changed."))
        super().save(*args, **kwargs)
