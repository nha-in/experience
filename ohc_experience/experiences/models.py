from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class CertificationAgency(models.Model):
    """An administrator-maintained certification agency for one program."""

    program = models.CharField(max_length=100, db_index=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(
        default=True,
        help_text=_("Deactivate an agency to remove it from new selections."),
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name", "pk"]
        verbose_name_plural = "certification agencies"
        constraints = [
            models.UniqueConstraint(
                fields=["program", "name"],
                name="unique_certification_agency_per_program",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        from .registry import registry  # noqa: PLC0415

        super().clean()
        if self.program not in {program.key for program in registry.programs()}:
            raise ValidationError({"program": "Choose a registered program."})


class AccessGrant(models.Model):
    """Explicit staff capabilities for one program, area, and category."""

    class Area(models.TextChoices):
        REVIEW = "review", _("Reviews")
        SUPPORT = "support", _("Support")
        EVENTS = "events", _("Events")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="experience_access",
    )
    program = models.CharField(max_length=100)
    area = models.CharField(max_length=20, choices=Area)
    category = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("General/onboarding, a program category, or all categories."),
    )
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)

    class Meta:
        ordering = ["user", "program", "area", "category"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "program", "area", "category"],
                name="unique_experience_access_grant",
            ),
            models.CheckConstraint(
                condition=Q(can_read=True) | Q(can_write=False, can_approve=False),
                name="experience_write_approve_requires_read",
            ),
        ]

    def __str__(self):
        return (
            f"{self.user}: {self.program} / {self.area} / {self.category or 'General'}"
        )

    def clean(self):
        from .registry import registry  # noqa: PLC0415

        super().clean()
        programs = {program.key: program for program in registry.programs()}
        if self.program not in programs:
            raise ValidationError({"program": "Choose a registered program."})
        if self.category not in {"", "*", *programs[self.program].track_map()}:
            raise ValidationError({"category": "Choose a category in this program."})
        if (self.can_write or self.can_approve) and not self.can_read:
            msg = "Write and approve permissions require read access."
            raise ValidationError(msg)


class Product(models.Model):
    """One organisation-owned product that can have many application workflows."""

    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(_("Product name"), max_length=255)
    slug = models.SlugField(_("Slug"), max_length=255)
    description = models.TextField(_("Product and intended use"))
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    #: Issued by the gateway team, which hands the secret to the integrator
    #: directly; staff add the ID once an exit is approved.
    production_client_id = models.CharField(
        _("Production client ID"),
        max_length=255,
        blank=True,
    )
    #: The day the gateway team issued the credentials, as staff entered it.
    production_issued_on = models.DateField(
        _("Production issue date"),
        null=True,
        blank=True,
    )
    #: When this portal saved the ID, which is not when it was issued.
    production_recorded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_experience_products",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "slug"],
                name="unique_product_slug_per_organisation",
            ),
            models.UniqueConstraint(
                Lower("production_client_id"),
                condition=~Q(production_client_id=""),
                name="unique_production_client_id",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return self.workspace.get_absolute_url()

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
    def organisation(self):
        return self.product.organisation


class ApplicationDependency(models.Model):
    """A prerequisite application that must complete before another is decided."""

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


class ProductWorkspace(models.Model):
    product = models.OneToOneField(
        "experiences.Product",
        on_delete=models.CASCADE,
        related_name="workspace",
    )
    reference = models.CharField(max_length=32, unique=True)
    experience_type = models.CharField(max_length=100)
    solution_type = models.JSONField(default=list, blank=True)
    applied_milestones = models.JSONField(default=list)
    registered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.reference} - {self.product.name}"

    def get_absolute_url(self):
        return reverse("experiences:overview", args=[self.reference])

    @property
    def definition(self):
        from .registry import get_program  # noqa: PLC0415

        return get_program(self.experience_type)

    def get_solution_type_display(self):
        labels = self.definition.solution_types
        return ", ".join(labels.get(key, key) for key in self.solution_type)


class Milestone(models.Model):
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    key = models.CharField(max_length=100)
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
                name="experience_unique_product_milestone",
            ),
        ]

    def __str__(self):
        return f"{self.product.name}: {self.definition.code}"

    @property
    def definition(self):
        return self.product.workspace.definition.milestones[self.key]

    @property
    def track_codes(self):
        return [
            track.code
            for track in self.product.workspace.definition.tracks_with(self.key)
        ]


class ReviewItem(models.Model):
    class Kind(models.TextChoices):
        ORGANISATION = "organisation_verification", "Organisation verification"
        PRODUCT = "product_registration", "Product registration"
        APPLICATION = "application", "Application request"

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
        related_name="review_items",
    )
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_items",
    )
    application = models.OneToOneField(
        "experiences.ApplicationInstance",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_item",
    )
    form = models.ForeignKey(
        "experiences.FormRecord",
        on_delete=models.PROTECT,
        related_name="review_items",
    )
    selected_submission = models.ForeignKey(
        "experiences.FormSubmission",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_items",
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
        related_name="assigned_review_items",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resubmission_count = models.PositiveIntegerField(default=0)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_review_items",
    )
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["submitted_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation"],
                condition=Q(kind="organisation_verification"),
                name="experience_one_org_review",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(kind="product_registration"),
                name="experience_one_product_review",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="organisation_verification",
                        product__isnull=True,
                        application__isnull=True,
                    )
                    | Q(
                        kind__in=["product_registration", "application"],
                        product__isnull=False,
                        application__isnull=False,
                    )
                ),
                name="review_item_subject_required",
            ),
        ]

    def __str__(self):
        return f"{self.reference} - {self.title}"

    def get_absolute_url(self):
        return reverse("experiences:review", args=[self.pk])

    @property
    def definition(self):
        from .registry import registry  # noqa: PLC0415

        return registry.get_form(self.form.form_key)

    @property
    def program(self):
        from .registry import get_program  # noqa: PLC0415

        if self.product_id:
            return self.product.workspace.definition
        return get_program(self.form.metadata.get("program"))

    @property
    def reference(self):
        return f"REV-{self.pk:05d}"

    @property
    def title(self):
        if self.kind == self.Kind.APPLICATION:
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
        related_name="review_questions",
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
        related_name="review_replies",
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
        related_name="audit_events",
    )
    product = models.ForeignKey(
        "experiences.Product",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
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


class ProductCredential(models.Model):
    product = models.OneToOneField(
        "experiences.Product",
        on_delete=models.PROTECT,
        related_name="credential",
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
    request_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    recipient = models.EmailField()
    from_email = models.CharField(max_length=254, blank=True)
    cc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    template_id = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(
        max_length=8,
        choices=[("info", "Information"), ("otp", "OTP")],
        default="info",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["next_attempt_at"],
                condition=Q(sent_at__isnull=True, failed_at__isnull=True),
                name="notification_pending_due",
            ),
        ]

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
                name="experience_unique_event_registration",
            ),
        ]

    def __str__(self):
        return f"{self.event.title}: {self.user.email}"


class TicketAttachment(models.Model):
    message = models.ForeignKey(
        "support.TicketMessage",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to="experience-support/%Y/%m/")
    original_name = models.CharField(max_length=255)

    def __str__(self):
        return self.original_name
