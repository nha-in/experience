"""Create DHIS handoff URLs from trusted, provider-compatible server data."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.text import capfirst

from ohc_experience.experiences.definitions import ProductHandoffDefinition
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductOutcomeStatus
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.permissions import require_integrator
from ohc_experience.integrations.dhis import DHISConfig
from ohc_experience.integrations.dhis import DHISConfigurationError
from ohc_experience.integrations.dhis import build_dhis_url

from .wasa import approved_wasa_submission
from .wasa import current_wasa

if TYPE_CHECKING:
    from collections.abc import Mapping


# Canonical product selections -> legacy solution and terminal milestone labels.
# This does not change the application's prerequisite graph.
SOLUTION_MILESTONES = {
    "hmis": ("HMIS", ("m1", "m2", "m3"), "M3"),
    "lmis": ("LMIS", ("m1", "m2"), "M2"),
    "telemedicine": ("Telemedicine", ("m1", "m2", "m3"), "M3"),
    "health_locker": ("HealthLocker", ("p1", "p2", "p3", "p4"), "Healthlocker"),
    "pharmacy": ("Pharmacy", ("m1", "m2"), "M2"),
}
SOLUTION_ALIASES = {"clinical_hmis": "hmis"}
MILESTONE_LABELS = {
    "m1": "M1",
    "m2": "M2",
    "m3": "M3",
    "p1": "PHR",
    "p2": "PHR",
    "p3": "PHR",
    "p4": "Health Locker",
}
UNAVAILABLE_MESSAGE = "DHIS is temporarily unavailable. Please try again later."


def _configuration():
    return DHISConfig(
        signing_secret=settings.ABDM_DHIS_JWT_SECRET,
        encryption_key=settings.ABDM_DHIS_AES_KEY,
        encryption_iv=settings.ABDM_DHIS_AES_IV,
        destination_url=settings.ABDM_DHIS_URL,
    )


def _fresh_product(product, actor):
    product = Product.objects.select_related(
        "workspace",
        "organisation",
        "created_by",
    ).get(pk=product.pk)
    require_integrator(actor, product.organisation)
    return product


def _approved_data(item):
    """An approved form can have a newer draft; use the reviewed revision."""
    decision = item.history.filter(action="Approved").first() if item else None
    submission = (
        FormSubmission.objects.filter(
            pk=decision.detail.get("submission_id"),
            form=item.form,
            origin_application=item.application,
            status="completed",
        ).first()
        if decision
        else None
    )
    if not submission:
        msg = "Approved registration details are required for DHIS."
        raise ValidationError(msg)
    return submission.data


def _recorded_data(item):
    """A product registration applies when it is submitted: its current details."""
    submission = item.selected_submission if item else None
    if (
        not submission
        or item.status != ReviewItem.Status.APPROVED
        or submission.status != "completed"
    ):
        msg = "Submitted product registration details are required for DHIS."
        raise ValidationError(msg)
    return submission.data


def _registration_data(product):
    if (
        product.workspace.experience_type != "abdm"
        or not product.organisation.is_verified
    ):
        noun = capfirst(product.organisation.noun)
        msg = f"{noun} verification must be approved for DHIS."
        raise ValidationError(msg)
    registration = _recorded_data(
        product.review_items.filter(kind=ReviewItem.Kind.PRODUCT).first(),
    )
    approved_types = {
        SOLUTION_ALIASES.get(value, value)
        for value in registration.get("solution_type", [])
    }
    organisation = _approved_data(
        ReviewItem.objects.filter(
            organisation=product.organisation,
            kind=ReviewItem.Kind.ORGANISATION,
            form__form_key="sandbox_organisation",
        ).first(),
    )
    return organisation, approved_types


def _approved_milestones(product):
    return set(
        product.outcomes.filter(
            Q(valid_until__isnull=True) | Q(valid_until__gte=timezone.localdate()),
            outcome_type="milestone_approval",
            status=ProductOutcomeStatus.ACTIVE,
            source_application__status="approved",
            source_application__milestone__enabled=True,
        ).values_list("source_application__milestone__key", flat=True),
    )


def _solution_error(solution_type, approved_types):
    if solution_type not in approved_types:
        return "This solution type is not in the product registration."
    return ""


def _milestone_error(required, approved):
    if missing := [key for key in required if key not in approved]:
        # The three PHR phases share one legacy label, so name it once.
        names = ", ".join(dict.fromkeys(MILESTONE_LABELS[key] for key in missing))
        return f"Complete the required milestone approvals before DHIS: {names}."
    return ""


def _require_wasa(product):
    approval = current_wasa(product)
    certificate = approved_wasa_submission(
        product,
        approval.data.get("submission_id") if approval else None,
    )
    if not certificate:
        msg = "The product needs a current, approved WASA certificate for DHIS."
        raise ValidationError(msg)
    return approval


class DHISHandoff(ProductHandoffDefinition):
    name = "DHIS"
    description = (
        "Register or manage claims under the Digital Health Incentive Scheme. "
        "Choose an approved solution type to continue."
    )
    action_label = "Continue to DHIS"

    @classmethod
    def options(cls, product, *, actor):
        product = _fresh_product(product, actor)
        approved_types, approved_milestones = set(), set()
        registration_error, wasa_error, configuration_error = "", "", ""
        try:
            _, approved_types = _registration_data(product)
        except ValidationError as error:
            registration_error = " ".join(error.messages)
        if not registration_error:
            approved_milestones = _approved_milestones(product)
            try:
                _require_wasa(product)
            except ValidationError as error:
                wasa_error = " ".join(error.messages)
        try:
            _configuration()
        except DHISConfigurationError:
            configuration_error = UNAVAILABLE_MESSAGE
        rows = []
        for key, (label, required, _) in SOLUTION_MILESTONES.items():
            reason = (
                registration_error
                or _solution_error(key, approved_types)
                or wasa_error
                or _milestone_error(required, approved_milestones)
                or configuration_error
            )
            rows.append(
                {
                    "key": key,
                    "label": "Health Locker" if key == "health_locker" else label,
                    "enabled": not reason,
                    "reason": reason,
                },
            )
        return tuple(rows)

    @classmethod
    def create_url(cls, product, *, option, actor):
        try:
            return create_product_handoff_url(
                product,
                solution_type=option,
                actor=actor,
            )
        except DHISConfigurationError:
            raise ValidationError(UNAVAILABLE_MESSAGE, code="unavailable") from None


def create_product_handoff_url(product, *, solution_type: str, actor) -> str:
    """Use the product ID and its latest approved WASA for a DHIS handoff.

    Read fresh state for every request, even when the caller retained a product
    instance across a renewal. Previous claims never prevent another handoff.
    """
    product = _fresh_product(product, actor)
    if solution_type not in SOLUTION_MILESTONES:
        msg = "Choose a supported DHIS solution type."
        raise ValidationError(msg)
    intent, required, milestone = SOLUTION_MILESTONES[solution_type]
    organisation, approved_types = _registration_data(product)
    if reason := _solution_error(solution_type, approved_types):
        raise ValidationError(reason)
    approval = _require_wasa(product)
    if reason := _milestone_error(required, _approved_milestones(product)):
        raise ValidationError(reason)
    # The legacy helper omitted wasa_start_date and top-level wasa_status.
    # Preserve that behavior rather than reinterpret an audit date as validity.
    payload = {
        "name": organisation["name"],
        "entity": organisation["name"],
        "entity_type": organisation["entity_type"],
        "ownership": organisation["entity_type"],
        "client_id": str(product.pk),
        "mobile": product.created_by.phone_number,
        "email": product.created_by.email,
        "addressLine1": organisation["registered_address"],
        "addressLine2": "",
        "city": organisation["district"],
        "state": organisation["state"],
        "district": organisation["district"],
        "pincode": organisation["pincode"],
        "intent_request": intent,
        "integration_level": milestone,
        "wasa_valid_upto_date": [
            {
                "milestone": milestone,
                "expiryDate": approval.valid_until.isoformat(),
                "status": approval.status,
            },
        ],
    }
    return create_handoff_url(payload)


def create_handoff_url(payload: Mapping[str, object]) -> str:
    """Encode verified identity/WASA fields; never pass browser input directly.

    Identity mapping, eligibility and WASA applicability belong to the caller.
    This function deliberately does not infer them from an OAuth client or an
    audit date. The legacy clock is Unix milliseconds multiplied by ten.
    """
    data = dict(payload)
    data["role"] = "sandbox"
    data["generatedTime"] = str((time.time_ns() // 1_000_000) * 10)
    return build_dhis_url(
        data,
        config=_configuration(),
    )
