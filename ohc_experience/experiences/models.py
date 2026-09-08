from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class ProductType(models.TextChoices):
    HMIS = "hmis", _("Hospital management information system (HMIS)")
    LMIS = "lmis", _("Laboratory management information system (LMIS)")
    EMR = "emr", _("Electronic medical record (EMR)")
    PHR = "phr_locker", _("Personal health record (PHR) application")
    HEALTH_LOCKER = "health_locker", _("Health locker")
    TELEMEDICINE = "telemedicine", _("Telemedicine platform")
    PHARMACY = "pharmacy", _("Pharmacy system")
    CLAIMS = "claims_platform", _("Health claims or insurance platform")
    HEALTH_SERVICES = (
        "health_services_platform",
        _("Health-service discovery or delivery platform"),
    )
    CONNECTOR = "connector", _("ABDM connector or middleware")
    OTHER = "other", _("Other digital health solution")


class ProductQuerySet(models.QuerySet["Product"]):
    def for_organisation(self, organisation) -> ProductQuerySet:
        return self.filter(organisation=organisation)

    def with_workspace_data(self) -> ProductQuerySet:
        return self.select_related("organisation", "created_by").prefetch_related(
            "applications",
            "form_records",
            "outcomes",
        )


class Product(models.Model):
    """One organisation-owned product that can have many application workflows."""

    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(_("Product name"), max_length=255)
    slug = models.SlugField(_("Slug"), max_length=255)
    product_type = models.CharField(
        _("Product type"),
        max_length=80,
        choices=ProductType,
    )
    description = models.TextField(_("Product and intended use"))
    website = models.URLField(_("Product website"), blank=True)
    current_facility_count = models.PositiveIntegerField(
        _("Facilities currently using the product"),
        default=0,
    )
    deployment_regions = models.TextField(
        _("Deployment states / union territories"),
        blank=True,
    )
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_experience_products",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ProductQuerySet] = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "slug"],
                name="unique_product_slug_per_organisation",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("products:detail", kwargs={"slug": self.slug})

    def can_edit(self, user) -> bool:
        return self.created_by_id == getattr(user, "pk", None) or (
            self.organisation.can_manage(user)
        )

    def _build_unique_slug(self) -> str:
        base = slugify(self.name)[:220] or "product"
        candidate = base
        suffix = 2
        taken = Product.objects.filter(organisation=self.organisation).exclude(
            pk=self.pk,
        )
        while taken.filter(slug=candidate).exists():
            candidate = f"{base[: 220 - len(str(suffix)) - 1]}-{suffix}"
            suffix += 1
        return candidate


class FormReuseScope(models.TextChoices):
    ORGANISATION = "organisation", _("Organisation")
    PRODUCT = "product", _("Product")
    APPLICATION = "application", _("Application only")


class FormRecordQuerySet(models.QuerySet["FormRecord"]):
    def for_product(self, product) -> FormRecordQuerySet:
        return self.filter(
            Q(product=product)
            | Q(
                organisation=product.organisation,
                reuse_scope=FormReuseScope.ORGANISATION,
            ),
        )

    def with_workspace_data(self) -> FormRecordQuerySet:
        return self.select_related(
            "organisation",
            "product",
            "created_by",
        ).prefetch_related(
            "submissions__attachments",
            "application_uses__application",
        )


class FormRecord(models.Model):
    """An independent form identity that applications can share."""

    reference = models.CharField(_("Reference"), max_length=40, unique=True)
    form_key = models.CharField(_("Form key"), max_length=100, db_index=True)
    name = models.CharField(_("Name"), max_length=255)
    reuse_scope = models.CharField(
        _("Reuse scope"),
        max_length=20,
        choices=FormReuseScope,
        default=FormReuseScope.PRODUCT,
        db_index=True,
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.CASCADE,
        related_name="experience_form_records",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="form_records",
    )
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_experience_form_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[FormRecordQuerySet] = FormRecordQuerySet.as_manager()

    class Meta:
        ordering = ["name", "created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "form_key"],
                condition=Q(reuse_scope=FormReuseScope.ORGANISATION),
                name="unique_organisation_form_record",
            ),
            models.UniqueConstraint(
                fields=["product", "form_key"],
                condition=Q(reuse_scope=FormReuseScope.PRODUCT),
                name="unique_product_form_record",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        reuse_scope=FormReuseScope.ORGANISATION,
                        product__isnull=True,
                    )
                    | Q(
                        reuse_scope__in=[
                            FormReuseScope.PRODUCT,
                            FormReuseScope.APPLICATION,
                        ],
                        product__isnull=False,
                    )
                ),
                name="form_record_scope_matches_product",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.name}"

    @property
    def current_submission(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("submissions")
        if prefetched is not None:
            return next((item for item in prefetched if item.is_current), None)
        return self.submissions.filter(is_current=True).first()

    def clean(self) -> None:
        super().clean()
        if self.product_id and self.product.organisation_id != self.organisation_id:
            raise ValidationError(
                _("The form product must belong to the form organisation."),
            )


class ApplicationQuerySet(models.QuerySet["ApplicationInstance"]):
    def for_organisation(self, organisation) -> ApplicationQuerySet:
        return self.filter(product__organisation=organisation)

    def visible_to(self, user) -> ApplicationQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False) or getattr(
            user,
            "is_ohc_team",
            False,
        ):
            return self
        return self.filter(
            Q(created_by=user) | Q(access_grants__user=user),
        ).distinct()

    def with_workspace_data(self) -> ApplicationQuerySet:
        return self.select_related(
            "product",
            "product__organisation",
            "created_by",
            "decided_by",
        ).prefetch_related(
            "form_uses__form__submissions__attachments",
            "form_uses__selected_submission",
            "access_grants__user",
            "query_threads",
            "dependencies",
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
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="applications",
    )
    forms = models.ManyToManyField(
        FormRecord,
        through="ApplicationFormUse",
        related_name="applications",
        blank=True,
    )
    dependencies = models.ManyToManyField(
        "self",
        through="ApplicationDependency",
        through_fields=("application", "depends_on"),
        symmetrical=False,
        related_name="dependent_applications",
        blank=True,
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
                fields=["product", "application_type", "status"],
                name="exp_product_type_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.title}"

    @property
    def progress_percent(self) -> int:
        return int(self.metadata.get("progress_percent", 0))

    @property
    def organisation(self):
        return self.product.organisation


class ApplicationDependency(models.Model):
    """A prerequisite application that must complete before another can submit."""

    application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.CASCADE,
        related_name="dependency_links",
    )
    depends_on = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.PROTECT,
        related_name="dependent_links",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "depends_on"],
                name="unique_application_dependency",
            ),
            models.CheckConstraint(
                condition=~Q(application=F("depends_on")),
                name="application_cannot_depend_on_itself",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.application.reference} depends on {self.depends_on.reference}"

    def clean(self) -> None:
        super().clean()
        if not self.application_id or not self.depends_on_id:
            return
        if self.application_id == self.depends_on_id:
            raise ValidationError(_("An application cannot depend on itself."))
        if self.application.product_id != self.depends_on.product_id:
            raise ValidationError(
                _("Application dependencies must belong to the same product."),
            )
        if self._creates_cycle():
            raise ValidationError(_("This dependency would create a cycle."))

    def _creates_cycle(self) -> bool:
        target_id = self.application_id
        pending = {self.depends_on_id}
        visited: set[int] = set()
        while pending:
            if target_id in pending:
                return True
            visited.update(pending)
            pending = (
                set(
                    ApplicationDependency.objects.filter(
                        application_id__in=pending,
                    ).values_list("depends_on_id", flat=True),
                )
                - visited
            )
        return False


class SubmissionStatus(models.TextChoices):
    COMPLETED = "completed", _("Completed")
    NEEDS_CHANGES = "needs_changes", _("Needs changes")


class FormSubmission(models.Model):
    """One immutable revision of an independent form record."""

    form = models.ForeignKey(
        FormRecord,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    origin_application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originated_form_submissions",
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
        ordering = ["-submission_number", "-revision", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "form",
                    "submission_number",
                    "revision",
                ],
                name="unique_form_submission_revision",
            ),
            models.UniqueConstraint(
                fields=["form"],
                condition=Q(is_current=True),
                name="unique_current_form_submission",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.form.reference} / {self.form_key} "
            f"#{self.submission_number} r{self.revision}"
        )

    def save(self, *args, **kwargs) -> None:
        if not self.form_key:
            self.form_key = self.form.form_key
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return bool(self.valid_until and self.valid_until < timezone.localdate())


class ApplicationFormUse(models.Model):
    """Links an application to a reusable form and its pinned revision."""

    application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.CASCADE,
        related_name="form_uses",
    )
    form = models.ForeignKey(
        FormRecord,
        on_delete=models.PROTECT,
        related_name="application_uses",
    )
    form_key = models.CharField(_("Form key"), max_length=100)
    selected_submission = models.ForeignKey(
        FormSubmission,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="application_uses",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "form_key"],
                name="unique_form_use_per_application",
            ),
            models.UniqueConstraint(
                fields=["application", "form"],
                name="unique_form_record_per_application",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.application.reference} uses {self.form.reference}"

    @property
    def is_reused(self) -> bool:
        return bool(
            self.selected_submission_id
            and self.selected_submission.origin_application_id
            and self.selected_submission.origin_application_id != self.application_id,
        )

    def clean(self) -> None:
        super().clean()
        if not self.application_id or not self.form_id:
            return
        if self.form_key != self.form.form_key:
            raise ValidationError(
                _("The application form key does not match the form."),
            )
        if self.application.product.organisation_id != self.form.organisation_id:
            raise ValidationError(
                _("Applications can only use forms from their organisation."),
            )
        if self.form.product_id and self.form.product_id != self.application.product_id:
            raise ValidationError(
                _("Applications can only use forms for the same product."),
            )
        if (
            self.selected_submission_id
            and self.selected_submission.form_id != self.form_id
        ):
            raise ValidationError(
                _("The selected submission must belong to the linked form."),
            )


class ProductOutcomeStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    EXPIRED = "expired", _("Expired")
    REVOKED = "revoked", _("Revoked")


class ProductOutcome(models.Model):
    """A structured result issued to a product by an application workflow."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="outcomes",
    )
    outcome_type = models.CharField(_("Outcome type"), max_length=100, db_index=True)
    name = models.CharField(_("Name"), max_length=255)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=ProductOutcomeStatus,
        default=ProductOutcomeStatus.ACTIVE,
        db_index=True,
    )
    data = models.JSONField(_("Structured data"), default=dict)
    field_schema = models.JSONField(_("Field schema"), default=list, blank=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    source_application = models.ForeignKey(
        ApplicationInstance,
        on_delete=models.PROTECT,
        related_name="product_outcomes",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_product_outcomes",
    )
    valid_until = models.DateField(null=True, blank=True, db_index=True)
    issued_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "outcome_type", "source_application"],
                name="unique_product_outcome_per_application",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} - {self.name}"

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


class FormAttachment(models.Model):
    """Versioned file evidence kept outside JSON submission payloads."""

    submission = models.ForeignKey(
        FormSubmission,
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
        FormSubmission,
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
    OUTCOME_ISSUED = "outcome_issued", _("Product outcome issued")
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
        FormSubmission,
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
