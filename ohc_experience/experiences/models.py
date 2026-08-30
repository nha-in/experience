from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ApplicationQuerySet(models.QuerySet["ApplicationInstance"]):
    def for_organisation(self, organisation) -> ApplicationQuerySet:
        return self.filter(organisation=organisation)

    def visible_to(self, user) -> ApplicationQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self
        return self.filter(
            Q(created_by=user) | Q(access_grants__user=user),
        ).distinct()

    def with_workspace_data(self) -> ApplicationQuerySet:
        return self.select_related(
            "organisation",
            "created_by",
            "decided_by",
        ).prefetch_related(
            "submissions",
            "access_grants__user",
            "query_threads",
        )


class ApplicationInstance(models.Model):
    """One persisted run of a code-defined application experience."""

    reference = models.CharField(_("Reference"), max_length=40, unique=True)
    application_type = models.CharField(
        _("Application type"),
        max_length=100,
        db_index=True,
    )
    title = models.CharField(_("Title"), max_length=255)
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="experience_applications",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_experience_applications",
    )
    status = models.CharField(_("Status"), max_length=50, db_index=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    outcome = models.JSONField(_("Outcome"), default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_experience_applications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ApplicationQuerySet] = ApplicationQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["organisation", "application_type", "status"],
                name="experience_org_type_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.title}"

    @property
    def progress_percent(self) -> int:
        return int(self.metadata.get("progress_percent", 0))


class SubmissionStatus(models.TextChoices):
    COMPLETED = "completed", _("Completed")
    NEEDS_CHANGES = "needs_changes", _("Needs changes")


class ApplicationFormSubmission(models.Model):
    """One immutable revision of a validated form submission."""

    application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    form_key = models.CharField(_("Form key"), max_length=100)
    status = models.CharField(
        _("Status"),
        max_length=30,
        choices=SubmissionStatus,
        default=SubmissionStatus.COMPLETED,
    )
    data = models.JSONField(_("Validated data"), default=dict)
    field_schema = models.JSONField(_("Field schema"), default=list, blank=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    schema_version = models.PositiveSmallIntegerField(default=1)
    revision = models.PositiveIntegerField(default=1)
    submission_number = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)
    valid_until = models.DateField(null=True, blank=True, db_index=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="experience_form_submissions",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["form_key", "-submission_number", "-revision"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "application",
                    "form_key",
                    "submission_number",
                    "revision",
                ],
                name="unique_experience_form_submission_revision",
            ),
            models.UniqueConstraint(
                fields=["application", "form_key"],
                condition=Q(is_current=True),
                name="unique_current_experience_form_submission",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.application.reference} / {self.form_key} "
            f"#{self.submission_number} r{self.revision}"
        )

    @property
    def is_expired(self) -> bool:
        return bool(self.valid_until and self.valid_until < timezone.localdate())


class ApplicationAccess(models.Model):
    """Application-scoped role and direct permission assignment for one user."""

    application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.CASCADE,
        related_name="access_grants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="experience_access_grants",
    )
    role_key = models.CharField(_("Application role"), max_length=80, blank=True)
    direct_permissions = models.JSONField(
        _("Additional permissions"),
        default=list,
        blank=True,
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_experience_access",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "user"],
                name="unique_user_access_per_application",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} / {self.application.reference} / {self.role_key}"


class ApplicationAttachment(models.Model):
    """Versioned file evidence kept outside JSON submission payloads."""

    submission = models.ForeignKey(
        ApplicationFormSubmission,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    field_key = models.CharField(max_length=100)
    file = models.FileField(upload_to="experience-attachments/%Y/%m/")
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=150, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    is_current = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="experience_attachments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["field_key", "-created_at"]
        indexes = [
            models.Index(
                fields=["submission", "field_key", "is_current"],
                name="exp_current_attachment_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.original_name


class QueryStatus(models.TextChoices):
    AWAITING_APPLICANT = "awaiting_applicant", _("Awaiting applicant")
    AWAITING_REVIEWER = "awaiting_reviewer", _("Awaiting reviewer")
    RESOLVED = "resolved", _("Resolved")


class ApplicationQueryThread(models.Model):
    application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.CASCADE,
        related_name="query_threads",
    )
    submission = models.ForeignKey(
        ApplicationFormSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="query_threads",
    )
    subject = models.CharField(max_length=255)
    status = models.CharField(
        max_length=30,
        choices=QueryStatus,
        default=QueryStatus.AWAITING_APPLICANT,
        db_index=True,
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="opened_application_queries",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_application_queries",
    )
    due_at = models.DateField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.application.reference}: {self.subject}"


class QueryMessageKind(models.TextChoices):
    MESSAGE = "message", _("Message")
    EVENT = "event", _("Event")


class ApplicationQueryMessage(models.Model):
    thread = models.ForeignKey(
        ApplicationQueryThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="application_query_messages",
    )
    body = models.TextField()
    kind = models.CharField(
        max_length=20,
        choices=QueryMessageKind,
        default=QueryMessageKind.MESSAGE,
    )
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.body[:80]


class EventKind(models.TextChoices):
    CREATED = "created", _("Created")
    FORM_SUBMITTED = "form_submitted", _("Form submitted")
    ACTION = "action", _("Action")
    STATUS_CHANGED = "status_changed", _("Status changed")
    ACCESS_CHANGED = "access_changed", _("Access changed")
    QUERY = "query", _("Query")


class ApplicationEvent(models.Model):
    """Append-only audit record for every meaningful application mutation."""

    application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.CASCADE,
        related_name="events",
    )
    submission = models.ForeignKey(
        ApplicationFormSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="experience_events",
    )
    kind = models.CharField(max_length=30, choices=EventKind)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    action_key = models.CharField(max_length=100, blank=True)
    status_before = models.CharField(max_length=50, blank=True)
    status_after = models.CharField(max_length=50, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.application.reference}: {self.title}"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            raise ValidationError(_("Application audit events cannot be changed."))
        super().save(*args, **kwargs)
