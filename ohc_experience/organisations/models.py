from __future__ import annotations

import secrets
from datetime import timedelta
from typing import ClassVar
from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

INVITATION_TTL = timedelta(days=14)
INVITATION_TOKEN_BYTES = 32

#: The one entity type that is a person trading under a name rather than a
#: registered company. It has no CIN, its name is a business name, and it is
#: the only type the portal lets sign up without a website.
SOLE_PROPRIETOR = "sole_proprietor"

#: A government body registers facilities and professionals under its own
#: authority, so M4 opens for it without M1.
GOVERNMENT = "government"


class Role(models.TextChoices):
    """Roles a member can hold inside an organisation.

    Ordered most to least privileged. The copy mirrors the team settings
    screen: developers see sandbox credentials and API resources, support sees
    the ticket inbox, admins manage the team, products and reviews.
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
    def for_user(self, user) -> OrganisationQuerySet:
        return self.filter(memberships__user=user)


class Organisation(models.Model):
    """An integrator company: the unit owning a sandbox, certifications and a team."""

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", _("Verification pending")
        VERIFIED = "verified", _("Verified integrator")
        REJECTED = "rejected", _("Verification rejected")
        WITHDRAWN = "withdrawn", _("Verification withdrawn")

    name = models.CharField(_("Organisation"), max_length=255)
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)

    # Company profile — collected during onboarding (screen 1b).
    legal_name = models.CharField(_("Legal entity name"), max_length=255, blank=True)
    entity_type = models.CharField(_("Type of entity"), max_length=32, blank=True)
    website = models.URLField(_("Website"), blank=True)
    city = models.CharField(_("City"), max_length=120, blank=True)
    state = models.CharField(_("State"), max_length=120, blank=True)
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
        return reverse("experiences:organisation")

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
    def noun(self) -> str:
        """What copy calls this organisation: a sole proprietorship is a business."""
        if self.entity_type == SOLE_PROPRIETOR:
            return _("business")
        return _("organisation")

    @property
    def website_domain(self) -> str:
        """The website's host without "www.": https://www.acme.in/about is acme.in."""
        address = self.website.strip()
        host = urlsplit(address if "://" in address else f"//{address}").hostname
        return (host or "").removeprefix("www.")

    def email_off_website(self, email: str) -> str:
        """The website's domain when `email` is not on it, else an empty string.

        Reviewers expect an address on the organisation's own domain, and
        js/email-domain-callout.js warns the integrator by the same rule as they
        fill in the form. A subdomain still belongs to the organisation:
        mail.acme.in is on acme.in. A sole proprietorship is the person, so a
        personal address is expected, and with no website there is nothing to
        hold the address against.
        """
        website = self.website_domain
        if self.entity_type == SOLE_PROPRIETOR or not website:
            return ""
        domain = email.rpartition("@")[2].lower()
        on_website = (
            domain == website
            or domain.endswith(f".{website}")
            or website.endswith(f".{domain}")
        )
        return "" if on_website else website

    @property
    def is_verified(self) -> bool:
        return self.verification_status == self.VerificationStatus.VERIFIED

    @property
    def verification_variant(self) -> str:
        """The badge variant for this status — one mapping, every screen.

        The integrator's settings page and the NHA console draw the same badge,
        and a three-way branch written out in each template is a branch that
        drifts. A rejection is amber, not red: it asks for corrections and a
        resubmission rather than closing the door.
        """
        return {
            self.VerificationStatus.VERIFIED: "success",
        }.get(self.verification_status, "warning")

    def set_verification(self, status: str) -> bool:
        """Record a decision or a withdrawal. True when something actually moved.

        `verified_at` is the date shown beside the badge, so it belongs to the
        verified state and to nothing else: an integrator moved back to pending
        or to rejected has it cleared, rather than left reading "Verified 3 Mar"
        under a badge that no longer says verified.
        """
        if self.verification_status == status:
            return False
        self.verification_status = status
        self.verified_at = (
            timezone.now() if status == self.VerificationStatus.VERIFIED else None
        )
        # modified_at is auto_now, and auto_now only fires for fields named in
        # update_fields.
        self.save(update_fields=["verification_status", "verified_at", "modified_at"])
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
