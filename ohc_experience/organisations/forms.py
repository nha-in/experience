from __future__ import annotations

import re

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ohc_experience.abdm.uploads import DOCUMENTS
from ohc_experience.abdm.uploads import IMAGE_MAX_MB
from ohc_experience.abdm.uploads import IMAGES
from ohc_experience.abdm.uploads import PDF_MAX_MB
from ohc_experience.abdm.uploads import ModelFileFormMixin
from ohc_experience.abdm.uploads import validate_upload

from .models import Invitation
from .models import Membership
from .models import Organisation
from .models import Role

# What each verification document number looks like, so a typo is caught at
# the form rather than by a reviewer ten days later.
DOCUMENT_NUMBER_PATTERNS = {
    Organisation.VerificationDocumentType.PAN: (
        re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$"),
        _("A PAN is ten characters: five letters, four digits, one letter."),
    ),
    Organisation.VerificationDocumentType.GSTIN: (
        re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"),
        _("A GSTIN is fifteen characters, starting with the two-digit state code."),
    ),
    Organisation.VerificationDocumentType.CIN: (
        re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$"),
        _("A CIN is twenty-one characters, starting with L or U."),
    ),
}

PINCODE_PATTERN = re.compile(r"^[1-9][0-9]{5}$")


class OrganisationProfileForm(ModelFileFormMixin, forms.ModelForm):
    """The organisation details form: identity, registered address, document.

    Used by onboarding step 2 and by Settings → Organisation. Every field the
    reviewer needs is required; website and logo are the optional ones.
    """

    download_kind = "organisation"

    REQUIRED_FIELDS = (
        "name",
        "description",
        "entity_type",
        "category",
        "registered_address",
        "pincode",
        "state",
        "district",
        "verification_document_type",
        "verification_document_number",
    )

    class Meta:
        model = Organisation
        fields = [
            "name",
            "description",
            "entity_type",
            "category",
            "website",
            "logo",
            "registered_address",
            "pincode",
            "state",
            "district",
            "verification_document_type",
            "verification_document_number",
            "verification_document",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "registered_address": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "verification_document_type": _("Document type"),
            "verification_document_number": _("Document number"),
            "verification_document": _("Supporting document"),
        }
        help_texts = {
            "description": _("What the organisation does and who it serves."),
            "logo": _("PNG, JPEG or WebP, up to 2 MB."),
            "verification_document": _(
                "The PAN card, GST certificate or certificate of incorporation. "
                "PDF or image, up to 10 MB.",
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for name in self.REQUIRED_FIELDS:
            self.fields[name].required = True
        self.fields["logo"].widget.attrs["accept"] = ".png,.jpg,.jpeg,.webp"
        self.fields["verification_document"].widget.attrs["accept"] = (
            ".pdf,.png,.jpg,.jpeg,.webp"
        )
        placeholders = {
            "website": "https://example.in",
            "pincode": "560001",
            "state": _("Karnataka"),
            "district": _("Bengaluru Urban"),
            "verification_document_number": "AAAAA1234A",
        }
        for name, placeholder in placeholders.items():
            self.fields[name].widget.attrs.setdefault("placeholder", placeholder)

    def clean_pincode(self) -> str:
        value = self.cleaned_data["pincode"].strip()
        if not PINCODE_PATTERN.match(value):
            msg = _("Enter a valid six-digit Indian pincode.")
            raise forms.ValidationError(msg)
        return value

    def clean_verification_document_number(self) -> str:
        return self.cleaned_data["verification_document_number"].strip().upper()

    def clean_logo(self):
        upload = self.cleaned_data.get("logo")
        validate_upload(upload, extensions=IMAGES, max_mb=IMAGE_MAX_MB)
        return upload

    def clean_verification_document(self):
        upload = self.cleaned_data.get("verification_document")
        validate_upload(upload, extensions=DOCUMENTS, max_mb=PDF_MAX_MB)
        return upload

    def clean(self):
        cleaned = super().clean()
        document_type = cleaned.get("verification_document_type")
        number = cleaned.get("verification_document_number")
        if document_type and number:
            pattern, message = DOCUMENT_NUMBER_PATTERNS[document_type]
            if not pattern.match(number):
                self.add_error("verification_document_number", message)
        if not self.has_error("verification_document") and not self.has_file(
            "verification_document",
        ):
            self.add_error(
                "verification_document",
                _("Upload the supporting document."),
            )
        return cleaned


class InvitationForm(forms.ModelForm):
    """Invite a teammate by email with a role."""

    class Meta:
        model = Invitation
        fields = ["email", "role"]
        labels = {"email": _("Email"), "role": _("Role")}

    def __init__(self, *args, organisation: Organisation, **kwargs):
        self.organisation = organisation
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = Role.assignable()
        self.fields["role"].initial = Role.DEVELOPER
        self.fields["email"].widget.attrs.setdefault(
            "placeholder",
            _("colleague@example.in"),
        )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower()
        if Membership.objects.filter(
            organisation=self.organisation,
            user__email__iexact=email,
        ).exists():
            msg = _("That person is already on your team.")
            raise forms.ValidationError(msg)
        if (
            Invitation.objects.filter(
                organisation=self.organisation,
                email__iexact=email,
            )
            .pending()
            .exists()
        ):
            msg = _("An invite is already pending for that address.")
            raise forms.ValidationError(msg)
        return email

    def save(self, commit=True) -> Invitation:  # noqa: FBT002
        invitation = super().save(commit=False)
        invitation.organisation = self.organisation
        # Reuse a spent or expired row for this address so the unique
        # constraint on open invites does not block a legitimate re-invite.
        # Expired rows count: they are still "open" as far as the constraint is
        # concerned, so a fresh insert for the same address would collide.
        stale = (
            Invitation.objects.filter(
                organisation=self.organisation,
                email__iexact=invitation.email,
            )
            .filter(
                Q(accepted_at__isnull=False)
                | Q(revoked_at__isnull=False)
                | Q(expires_at__lte=timezone.now()),
            )
            .first()
        )
        if stale is not None:
            stale.email = invitation.email
            stale.role = invitation.role
            stale.accepted_at = None
            stale.revoked_at = None
            stale.invited_by = invitation.invited_by
            stale.refresh_token()
            if commit:
                stale.save()
            return stale
        if commit:
            invitation.save()
        return invitation


class MembershipRoleForm(forms.ModelForm):
    """Change one member's role from the team table."""

    class Meta:
        model = Membership
        fields = ["role"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = Role.assignable()

    def clean_role(self) -> str:
        role = self.cleaned_data["role"]
        if self.instance.is_owner:
            msg = _("The owner's role cannot be changed.")
            raise forms.ValidationError(msg)
        return role
