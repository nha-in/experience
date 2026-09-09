from __future__ import annotations

import secrets
from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

INVITATION_TTL = timedelta(days=14)
INVITATION_TOKEN_BYTES = 32


class Role(models.TextChoices):
    """Roles a member can hold inside an organisation.

    Ordered most to least privileged. The copy mirrors the hub's team settings
    screen: developers see sandbox credentials and API resources, support sees
    the inbox, admins manage everything except billing and legal info.
    """

    OWNER = "owner", _("Owner")
    ADMIN = "admin", _("Admin")
    DEVELOPER = "developer", _("Developer")
    SUPPORT = "support", _("Support")

    @classmethod
    def assignable(cls) -> list[tuple[str, str]]:
        """Roles that can be handed out through the invite and role pickers.

        Ownership transfers are deliberately excluded — there is exactly one
        owner and moving it is a separate, destructive action.
        """
        return [(value, label) for value, label in cls.choices if value != cls.OWNER]


# Roles allowed to manage the organisation profile and the team roster.
MANAGER_ROLES = frozenset({Role.OWNER, Role.ADMIN})

# Most to least privileged — the order the team roster is listed in.
ROLE_ORDER = [Role.OWNER, Role.ADMIN, Role.DEVELOPER, Role.SUPPORT]


class OrganisationQuerySet(models.QuerySet["Organisation"]):
    def for_console(self) -> OrganisationQuerySet:
        """The OHC console's list: undecided vendors first, then alphabetical.

        A vendor waiting on verification is the only row on that screen anyone
        has to act on, so it floats to the top rather than sitting wherever the
        alphabet put it. Everything already decided keeps the usual name order.
        """
        return self.annotate(
            member_count=models.Count("memberships", distinct=True),
            # Ordering on verification_status itself would sort by the stored
            # value — pending, rejected, verified — which puts rejected vendors
            # above verified ones for no reason a reader could guess. Rank the
            # one distinction that matters instead.
            triage_rank=models.Case(
                models.When(
                    verification_status=Organisation.VerificationStatus.PENDING,
                    then=models.Value(0),
                ),
                default=models.Value(1),
                output_field=models.PositiveSmallIntegerField(),
            ),
        ).order_by("triage_rank", "name")


class Organisation(models.Model):
    """An integrator organisation: owns products, credentials and a team."""

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", _("Verification pending")
        VERIFIED = "verified", _("Verified")
        SENT_BACK = "sent_back", _("Sent back")

    class EntityType(models.TextChoices):
        PRIVATE_COMPANY = "private_company", _("Private company")
        GOVERNMENT_BODY = "government_body", _("Government body")
        SOLE_PROPRIETORSHIP = "sole_proprietorship", _("Sole proprietorship")
        TRUST_OR_SOCIETY = "trust_or_society", _("Trust or society")
        SECTION_8 = "section_8", _("Section 8 company")
        LLP = "llp", _("Limited liability partnership")

    class Category(models.TextChoices):
        INDIA_ENTITY = "india_entity", _("Entity in India")
        FOREIGN_WITH_INDIAN_SUBSIDIARY = (
            "foreign_with_indian_subsidiary",
            _("Foreign entity with an Indian subsidiary"),
        )
        ACADEMIC = "academic", _("Academic or research institution")

    class VerificationDocumentType(models.TextChoices):
        PAN = "PAN", "PAN"
        GSTIN = "GSTIN", "GSTIN"
        CIN = "CIN", "CIN"

    name = models.CharField(_("Name of the entity"), max_length=255)
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)

    # Identity — the ABDM organisation details form (screen 5.2).
    description = models.TextField(_("Description"), blank=True)
    entity_type = models.CharField(
        _("Type of entity"),
        max_length=30,
        choices=EntityType.choices,
        blank=True,
    )
    category = models.CharField(
        _("Category"),
        max_length=40,
        choices=Category.choices,
        blank=True,
    )
    logo = models.ImageField(
        _("Logo"),
        upload_to="organisations/logos/%Y/%m/",
        blank=True,
    )

    # Registered address.
    registered_address = models.TextField(_("Registered address"), blank=True)
    pincode = models.CharField(_("Pincode"), max_length=6, blank=True)
    district = models.CharField(_("District"), max_length=120, blank=True)

    # Verification document.
    verification_document_type = models.CharField(
        _("Verification document type"),
        max_length=10,
        choices=VerificationDocumentType.choices,
        blank=True,
    )
    verification_document_number = models.CharField(
        _("Verification document number"),
        max_length=40,
        blank=True,
    )
    verification_document = models.FileField(
        _("Supporting document"),
        upload_to="organisations/verification/%Y/%m/",
        blank=True,
    )
    verification_submitted_at = models.DateTimeField(
        _("Submitted for verification at"),
        null=True,
        blank=True,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_organisations",
        verbose_name=_("Verified by"),
    )
    verification_reason = models.TextField(
        _("Send-back reason"),
        blank=True,
        help_text=_("Shown to the integrator when verification is sent back."),
    )

    # Company profile — collected during onboarding (screen 1b).
    legal_name = models.CharField(_("Legal entity name"), max_length=255, blank=True)
    website = models.URLField(_("Website"), blank=True)
    city = models.CharField(_("City"), max_length=120, blank=True)
    state = models.CharField(_("State"), max_length=120, blank=True)
    deployment_regions = models.TextField(
        _("Deployment regions"),
        blank=True,
        help_text=_("States/UTs where you deploy or plan to deploy Care."),
    )

    # Technical contact — receives sandbox resets, credential rotations, changelogs.
    technical_contact_name = models.CharField(
        _("Technical contact name"),
        max_length=255,
        blank=True,
    )
    technical_contact_email = models.EmailField(
        _("Technical contact email"),
        blank=True,
    )
    technical_contact_phone = models.CharField(
        _("Technical contact phone number"),
        max_length=32,
        blank=True,
    )

    verification_status = models.CharField(
        _("Verification status"),
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    verified_at = models.DateTimeField(_("Verified at"), null=True, blank=True)
    onboarded_at = models.DateTimeField(_("Onboarded at"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[OrganisationQuerySet] = OrganisationQuerySet.as_manager()

    class Meta:
        verbose_name = _("Organisation")
        verbose_name_plural = _("Organisations")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("organisations:detail")

    def _build_unique_slug(self) -> str:
        base = slugify(self.name)[:200] or "organisation"
        candidate = base
        suffix = 2
        taken = Organisation.objects.exclude(pk=self.pk)
        while taken.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @property
    def display_name(self) -> str:
        return self.legal_name or self.name

    @property
    def is_verified(self) -> bool:
        return self.verification_status == self.VerificationStatus.VERIFIED

    @property
    def verification_variant(self) -> str:
        """The badge variant for this status — one mapping, every screen.

        The vendor's settings page and the OHC console draw the same badge, and
        a three-way branch written out in each template is a branch that drifts.
        """
        return {
            self.VerificationStatus.VERIFIED: "success",
            self.VerificationStatus.SENT_BACK: "destructive",
        }.get(self.verification_status, "warning")

    @property
    def is_pending(self) -> bool:
        return self.verification_status == self.VerificationStatus.PENDING

    @property
    def is_sent_back(self) -> bool:
        return self.verification_status == self.VerificationStatus.SENT_BACK

    @property
    def has_submitted_details(self) -> bool:
        return self.verification_submitted_at is not None

    # The details a reviewer needs before verification can be decided, in the
    # order the form asks for them. Website and logo are optional.
    REQUIRED_DETAILS = (
        ("name", _("Name of the entity")),
        ("description", _("Description")),
        ("entity_type", _("Type of entity")),
        ("category", _("Category")),
        ("registered_address", _("Registered address")),
        ("pincode", _("Pincode")),
        ("state", _("State")),
        ("district", _("District")),
        ("verification_document_type", _("Verification document type")),
        ("verification_document_number", _("Verification document number")),
        ("verification_document", _("Supporting document")),
    )

    @property
    def missing_details(self) -> list[str]:
        return [
            str(label)
            for name, label in self.REQUIRED_DETAILS
            if not getattr(self, name)
        ]

    @property
    def details_complete(self) -> bool:
        return not self.missing_details

    @property
    def verification_review_item(self):
        """The organisation_verification review item, or None before submission."""
        return self.review_items.filter(item_type="organisation_verification").first()

    def set_verification(self, status: str, *, actor=None, reason: str = "") -> bool:
        """Record the reviewer's decision. True when something actually moved.

        `verified_at` is the date shown beside the badge, so it belongs to the
        verified state and to nothing else: an organisation moved back to
        pending or sent back has it cleared, rather than left reading
        "Verified 3 Mar" under a badge that no longer says verified.
        """
        if self.verification_status == status:
            return False
        self.verification_status = status
        verified = status == self.VerificationStatus.VERIFIED
        self.verified_at = timezone.now() if verified else None
        self.verified_by = actor if verified else None
        self.verification_reason = (
            reason if status == self.VerificationStatus.SENT_BACK else ""
        )
        # modified_at is auto_now, and auto_now only fires for fields named in
        # update_fields.
        self.save(
            update_fields=[
                "verification_status",
                "verified_at",
                "verified_by",
                "verification_reason",
                "modified_at",
            ],
        )
        return True

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None

    @property
    def initials(self) -> str:
        return initials_for(self.name)

    def mark_onboarded(self) -> None:
        if self.onboarded_at is None:
            self.onboarded_at = timezone.now()

    def membership_for(self, user) -> Membership | None:
        if not getattr(user, "is_authenticated", False):
            return None
        return self.memberships.filter(user=user).select_related("user").first()

    def role_of(self, user) -> str | None:
        membership = self.membership_for(user)
        return membership.role if membership else None

    def can_manage(self, user) -> bool:
        return self.role_of(user) in MANAGER_ROLES

    @property
    def owner(self):
        membership = (
            self.memberships.filter(role=Role.OWNER).select_related("user").first()
        )
        return membership.user if membership else None


class Membership(models.Model):
    """Links a user to an organisation with a role."""

    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("Organisation"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("User"),
    )
    role = models.CharField(
        _("Role"),
        max_length=20,
        choices=Role,
        default=Role.DEVELOPER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Membership")
        verbose_name_plural = _("Memberships")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "user"],
                name="unique_membership_per_organisation",
            ),
        ]
        # Owner first, then admins, developers, support, then alphabetical.
        # Role values do not sort that way alphabetically, so rank them.
        ordering = [
            models.Case(
                *[
                    models.When(role=role, then=models.Value(rank))
                    for rank, role in enumerate(ROLE_ORDER)
                ],
                default=models.Value(len(ROLE_ORDER)),
                output_field=models.PositiveSmallIntegerField(),
            ),
            "user__name",
            "user__email",
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.get_role_display()} @ {self.organisation}"

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def can_manage(self) -> bool:
        return self.role in MANAGER_ROLES

    @property
    def initials(self) -> str:
        return initials_for(self.user.name or self.user.email)


class InvitationQuerySet(models.QuerySet["Invitation"]):
    def pending(self) -> InvitationQuerySet:
        return self.filter(
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )


class Invitation(models.Model):
    """A pending invite to join an organisation, redeemed through a token URL."""

    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="invitations",
        verbose_name=_("Organisation"),
    )
    email = models.EmailField(_("Email"))
    role = models.CharField(
        _("Role"),
        max_length=20,
        choices=Role,
        default=Role.DEVELOPER,
    )
    token = models.CharField(_("Token"), max_length=64, unique=True, editable=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
        verbose_name=_("Invited by"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[InvitationQuerySet] = InvitationQuerySet.as_manager()

    class Meta:
        verbose_name = _("Invitation")
        verbose_name_plural = _("Invitations")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "email"],
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="unique_open_invitation_per_email",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email} → {self.organisation}"

    def save(self, *args, **kwargs) -> None:
        if not self.token:
            self.token = secrets.token_urlsafe(INVITATION_TOKEN_BYTES)
        if not self.expires_at:
            self.expires_at = timezone.now() + INVITATION_TTL
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("organisations:invitation-accept", kwargs={"token": self.token})

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_pending(self) -> bool:
        return (
            self.accepted_at is None and self.revoked_at is None and not self.is_expired
        )

    @property
    def initials(self) -> str:
        return initials_for(self.email)

    def refresh_token(self) -> None:
        """Reissue the invite — used by the "Resend invite" action."""
        self.token = secrets.token_urlsafe(INVITATION_TOKEN_BYTES)
        self.expires_at = timezone.now() + INVITATION_TTL

    def accept(self, user) -> Membership:
        membership, _created = Membership.objects.get_or_create(
            organisation=self.organisation,
            user=user,
            defaults={"role": self.role},
        )
        self.accepted_at = timezone.now()
        self.save(update_fields=["accepted_at"])
        return membership


class Sandbox(models.Model):
    """A vendor team's single Care sandbox, provisioned by the OHC team."""

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        PROVISIONING = "provisioning", _("Provisioning")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    organisation = models.OneToOneField(
        Organisation,
        on_delete=models.CASCADE,
        related_name="sandbox",
        verbose_name=_("Organisation"),
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.REQUESTED,
    )
    facility_name = models.CharField(_("Facility name"), max_length=255, blank=True)
    is_facility_empty = models.BooleanField(_("Empty facility"), default=False)

    job_id = models.CharField(_("Plugin job id"), max_length=64, blank=True)
    result = models.JSONField(_("Result"), default=dict, blank=True)
    error = models.TextField(_("Error"), blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_sandboxes",
    )
    provisioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provisioned_sandboxes",
    )

    requested_at = models.DateTimeField(auto_now_add=True)
    provisioned_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Sandbox")
        verbose_name_plural = _("Sandboxes")
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"Sandbox for {self.organisation} ({self.status})"

    @property
    def is_pending(self) -> bool:
        return self.status in {self.Status.REQUESTED, self.Status.PROVISIONING}

    @property
    def status_variant(self) -> str:
        """The badge variant, so every screen agrees on what a status looks like."""
        return {
            self.Status.REQUESTED: "neutral",
            self.Status.PROVISIONING: "info",
            self.Status.READY: "success",
            self.Status.FAILED: "destructive",
        }.get(self.status, "neutral")

    @property
    def credentials(self) -> list:
        return (self.result or {}).get("users", [])

    @property
    def primary_credential(self) -> dict | None:
        for credential in self.credentials:
            if credential.get("is_primary"):
                return credential
        return self.credentials[0] if self.credentials else None

    @property
    def loaded_data(self) -> dict:
        return (self.result or {}).get("loaded_data", {})

    @property
    def loaded_data_counts(self) -> dict:
        """Readable counts: the plugin also reports flags nobody can act on."""
        return {
            key.replace("_", " ").title(): value
            for key, value in self.loaded_data.items()
            if not isinstance(value, bool)
        }


def initials_for(value: str) -> str:
    """Two-letter initials for avatar chips, matching the mockup's AS/MK/RN chips."""
    cleaned = (value or "").strip()
    if not cleaned:
        return "?"
    local = cleaned.split("@")[0]
    parts = [part for part in local.replace(".", " ").replace("_", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
