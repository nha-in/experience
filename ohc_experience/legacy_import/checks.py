"""Rows the portal would not have written."""

from allauth.account.models import EmailAddress
from cryptography.fernet import InvalidToken
from django.db.models import Count
from django.db.models import F
from django.db.models import Q

from ohc_experience.abdm.catalog import EXCLUSIVE_TRACKS
from ohc_experience.abdm.catalog import MILESTONES
from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.abdm.reject_reasons import EXIT_REJECT_REASONS
from ohc_experience.experiences import legacy
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormRecord
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Milestone
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.secrets import cipher
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisioningRun
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.models import User

from . import clean
from . import writer

APPLICATION_STATUS = {
    ReviewItem.Status.APPROVED: {"approved"},
    ReviewItem.Status.REJECTED: {"draft"},
    ReviewItem.Status.IN_REVIEW: {"under_review"},
    ReviewItem.Status.QUERY: {"query_raised"},
    ReviewItem.Status.DRAFT: {"draft"},
}


def _wasa_outcomes_without_a_certificate():
    """WASA approvals whose certificate cannot be reached from the outcome.

    Legacy held no file for some of them, and the outcome says so itself. The
    rest name the submission they were read from, and its own entry holds the
    attachment: a certificate carried forward from an earlier milestone belongs
    to that earlier submission, not to the application this outcome sits under.
    """
    outcomes = list(
        ProductOutcome.objects.filter(outcome_type="wasa_approval").values_list(
            "pk",
            "data",
            "metadata",
        ),
    )
    named = {data.get("submission_id") for _, data, _ in outcomes}
    carried = {
        pk: (data.get("wasa_certificate") or {}).get("attachment_id")
        for pk, data in FormSubmission.objects.filter(pk__in=named).values_list(
            "pk",
            "data",
        )
    }
    held = set(
        FormAttachment.objects.filter(
            pk__in={value for value in carried.values() if value},
            field_key="wasa_certificate",
        ).values_list("pk", flat=True),
    )
    return [
        pk
        for pk, data, metadata in outcomes
        if metadata.get("legacy_note") != writer.WASA_NONE
        and carried.get(data.get("submission_id")) not in held
    ]


def _status_mismatches():
    reviews = ReviewItem.objects.filter(application__isnull=False)
    return [
        item.pk
        for item in reviews.select_related("application")
        if item.application.status not in APPLICATION_STATUS[item.status]
    ]


def _milestones_not_applied():
    return [
        milestone.pk
        for milestone in Milestone.objects.select_related("product")
        if not any(
            value.endswith(f":{milestone.key}")
            for value in milestone.product.applied_milestones
        )
    ]


def _verified_without_approval():
    approved = AuditEvent.objects.filter(
        item__kind=ReviewItem.Kind.ORGANISATION,
        action="Approved",
        detail__submission_id__isnull=False,
    ).values("organisation_id")
    return Organisation.objects.filter(verification_status="verified").exclude(
        pk__in=approved,
    )


def _undecryptable_credentials():
    unreadable = []
    for credential in ProductCredential.objects.all():
        try:
            cipher().decrypt(credential.encrypted_secret.encode())
        except InvalidToken:
            unreadable.append(credential.pk)
    return unreadable


def _answers_the_forms_dropped():
    """Answers stored under a field the form no longer declares.

    Every submission this script writes is built by hand rather than validated,
    so a field the portal renamed or removed is silent until someone opens the
    saved answers and finds a gap.
    """
    fields = {
        key: set(registry.get_form(key).form_class.base_fields)
        for key in FormSubmission.objects.values_list("form_key", flat=True).distinct()
        if key
    }
    return [
        submission.pk
        for submission in FormSubmission.objects.only("pk", "form_key", "data")
        if set(submission.data) - fields.get(submission.form_key, set(submission.data))
    ]


def target_drift():
    """What this script writes that the program has since moved on from.

    Legacy words are mapped onto the portal's own choices, and those choices
    live in the experience checkout. Run this before an import: a milestone or
    a solution type that has been renamed would otherwise import quietly and
    only show up as a product nobody can edit.
    """
    solutions = dict(ProductRegistrationForm.base_fields["solution_type"].choices)
    entities = dict(OrganisationForm.base_fields["entity_type"].choices)
    keys = {
        *clean.MILESTONE_ORDER,
        *clean.MILESTONE_TRACK,
        *(key for keys in clean.MILESTONE_TOKENS.values() for key in keys),
        *(key for keys in clean.REGISTRATION_TRACKS.values() for key in keys),
        *clean.DECLARATION_DATES,
    }
    order = {key: index for index, key in enumerate(clean.MILESTONE_ORDER)}
    problems = [
        f"solution type {slug!r} is no longer offered"
        for slug in clean.SOLUTION_ORDER
        if slug not in solutions
    ]
    problems += [
        f"entity type {slug!r} is no longer offered"
        for slug in sorted({*clean.ENTITY_TYPES.values(), "sole_proprietor"})
        if slug not in entities
    ]
    problems += [
        f"milestone {key!r} is no longer in the catalog"
        for key in sorted(keys)
        if key not in MILESTONES
    ]
    problems += [
        f"track {code!r} no longer exists"
        for code in sorted(set(clean.MILESTONE_TRACK.values()))
        if code not in TRACK_MAP
    ]
    problems += [
        f"milestone {key!r} is no longer in track {code!r}"
        for key, code in sorted(clean.MILESTONE_TRACK.items())
        if key in MILESTONES and code in TRACK_MAP and key not in TRACK_MAP[code].keys
    ]
    problems += [
        f"milestone {key!r} is ordered before {predecessor!r}, which it builds on"
        for key in clean.MILESTONE_ORDER
        if key in MILESTONES
        for predecessor in MILESTONES[key].predecessors
        if order[key] < order.get(predecessor, -1)
    ]
    problems += [
        f"milestone {key!r} is missing from the import's order"
        for key in sorted(MILESTONES)
        if key not in order
    ]
    problems += [
        f"reject reason {reason!r} is no longer offered for an exit"
        for reason in clean.EXIT_REASONS
        if reason not in EXIT_REJECT_REASONS
    ]
    return problems


def _checks():
    registrations = Count(
        "review_items",
        filter=Q(review_items__kind=ReviewItem.Kind.PRODUCT),
    )
    current = Count("submissions", filter=Q(submissions__is_current=True))
    pinned = ReviewItem.objects.filter(
        application__isnull=False,
        selected_submission__isnull=False,
    )
    return {
        "products without exactly one registration review": Product.objects.annotate(
            registrations=registrations,
        ).exclude(registrations=1),
        "products without a reference": Product.objects.filter(reference__isnull=True),
        "form records with submissions but none current": FormRecord.objects.annotate(
            total=Count("submissions"),
            current=current,
        ).filter(total__gt=0, current=0),
        "reviews pinned differently from their form use": pinned.exclude(
            application__form_uses__selected_submission=F("selected_submission"),
        ),
        "application status disagrees with its review": _status_mismatches(),
        "milestones without a review item": Milestone.objects.filter(
            application__review_item__isnull=True,
        ),
        "milestones missing from applied milestones": _milestones_not_applied(),
        "approved exits without an approval outcome": ReviewItem.objects.filter(
            status=ReviewItem.Status.APPROVED,
            application__application_type="abdm_sandbox_exit",
        ).exclude(application__product_outcomes__outcome_type="milestone_approval"),
        "approved reviews without a pinned submission": ReviewItem.objects.filter(
            status=ReviewItem.Status.APPROVED,
            selected_submission__isnull=True,
        ),
        "verified organisations without an Approved event": (
            _verified_without_approval()
        ),
        "Keycloak rows without a finished run": ProvisionedResource.objects.filter(
            system="KEYCLOAK",
        ).exclude(product__provisioning_runs__status=ProvisioningRun.Status.READY),
        "credentials the current key cannot decrypt": _undecryptable_credentials(),
        "pending notifications": Notification.objects.filter(
            sent_at__isnull=True,
            failed_at__isnull=True,
        ),
        "users without a verified primary email": User.objects.exclude(
            emailaddress__verified=True,
            emailaddress__primary=True,
        ),
        # An unverified number is an SMS code at the first sign-in, for everyone
        # legacy had a number for.
        "users whose mobile number is unverified": User.objects.exclude(
            phone_number="",
        ).filter(phone_verified=False),
        "email addresses that differ from their user": EmailAddress.objects.exclude(
            email=F("user__email"),
        ),
        "WASA outcomes without a certificate": (_wasa_outcomes_without_a_certificate()),
        # A registration that declared both keeps both, marked as a gap the
        # portal reads; anything else holding two tracks is a mistake.
        "products holding two exclusive tracks without the gap": Product.objects.filter(
            milestones__key__in=TRACK_MAP[EXCLUSIVE_TRACKS[0]].keys,
        )
        .filter(milestones__key__in=TRACK_MAP[EXCLUSIVE_TRACKS[1]].keys)
        .exclude(metadata__legacy_gaps__contains=legacy.TRACKS)
        .distinct(),
        "submissions without a form key": FormSubmission.objects.filter(form_key=""),
        "form uses without a form key": ApplicationFormUse.objects.filter(form_key=""),
        "answers under a field the form dropped": _answers_the_forms_dropped(),
    }


def run_checks(out):
    failed = {}
    for name, found in _checks().items():
        keys = (
            found
            if isinstance(found, list)
            else list(found.values_list("pk", flat=True))
        )
        out.write(f"{'BAD' if keys else 'ok '} {name}: {len(keys)}")
        if keys:
            failed[name] = keys[:10]
    out.write(
        f"{User.objects.count()} users, "
        f"{Organisation.objects.count()} organisations, "
        f"{Product.objects.count()} products",
    )
    return failed
