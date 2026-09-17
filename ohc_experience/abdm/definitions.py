from django.db.models import Q
from django.utils import timezone

from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.definitions import ApplicationSet
from ohc_experience.experiences.definitions import OutcomeDefinition
from ohc_experience.experiences.definitions import Prerequisite
from ohc_experience.experiences.definitions import ProgramDefinition
from ohc_experience.experiences.models import FormReuseScope
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.workflows import project_product
from ohc_experience.integrations.services import start_provisioning
from ohc_experience.organisations.models import Organisation

from .catalog import MILESTONES
from .catalog import TRACKS
from .dhis import DHISHandoff
from .forms import ExitEvidenceForm
from .forms import OrganisationForm
from .forms import ProductRegistrationForm
from .forms import UhiParticipationForm
from .forms import WasaReviewForm
from .gateway import ABDMProductionCredentials
from .gateway import ABDMSandboxCredentials
from .reference import ABDMReferenceEnvironment
from .wasa import preferred_wasa_submission
from .wasa import wasa_approval_block_reason
from .wasa import wasa_approval_outcomes
from .wasa import wasa_context
from .wasa_extraction import WASA_CERTIFICATE_FIELD
from .wasa_extraction import extract_certificate


def read_wasa_certificate(field_key, upload):
    """Offer the audit fields printed on the certificate the user just chose."""
    if field_key != WASA_CERTIFICATE_FIELD:
        return {}
    return extract_certificate(upload)


def organisation_prerequisite(item):
    """Milestones are submitted at any time but decided once verification is done."""
    if item.organisation.is_verified:
        return ()
    return (
        Prerequisite(
            f"{item.organisation.noun} verification",
            ReviewItem.objects.filter(
                organisation=item.organisation,
                kind=ReviewItem.Kind.ORGANISATION,
            ).first(),
        ),
    )


#: The reviews `organisation_prerequisite` holds, as a filter.
UNVERIFIED_ORGANISATION = ~Q(
    organisation__verification_status=Organisation.VerificationStatus.VERIFIED,
)


class OrganisationVerification(ApplicationFormDefinition):
    key = "sandbox_organisation"
    name = "Organisation verification"
    reuse_scope = FormReuseScope.ORGANISATION
    form_class = OrganisationForm
    allow_approved_updates = True

    @classmethod
    def initial_data(cls, item):
        return {
            "name": item.organisation.name,
            "website": item.organisation.website,
            "entity_type": item.organisation.entity_type or "private_company",
        }

    @classmethod
    def on_submit(cls, item, data, actor):
        org = item.organisation
        org.name = org.legal_name = data["name"]
        org.entity_type = data["entity_type"]
        org.website, org.state, org.city = (
            data["website"],
            data["state"],
            data["district"],
        )
        org.onboarded_at = org.onboarded_at or timezone.now()
        org.verification_status = "pending"
        org.verified_at = None
        org.save()

    @classmethod
    def on_approve(cls, item, actor):
        item.organisation.set_verification("verified")
        return ()

    @classmethod
    def on_send_back(cls, item, actor):
        item.organisation.set_verification("sent_back")


class ExitEvidence(ApplicationFormDefinition):
    key = "sandbox_exit_evidence"
    name = "Sandbox exit evidence"
    form_class = ExitEvidenceForm
    allow_reuse = True
    request_label = "exit request"
    submit_label = "Request for exit"
    submitted_message = "Exit requested."
    approval_notice = (
        "The NHA gateway team issues production credentials. Your production "
        "client ID appears on the Credentials page once it is recorded."
    )

    @classmethod
    def form_kwargs(cls, item):
        return {
            "product": item.product,
            "wasa_source_submission": preferred_wasa_submission(item),
            "prefer_product_wasa": (
                not item.selected_submission
                or item.selected_submission.origin_application_id != item.application_id
            ),
        }

    @classmethod
    def snapshot_valid_until(cls, form):
        return form.cleaned_data.get("wasa_valid_until")

    @classmethod
    def read_document(cls, field_key, upload):
        return read_wasa_certificate(field_key, upload)

    @classmethod
    def approval_block_reason(cls, item):
        return wasa_approval_block_reason(item)

    @classmethod
    def pending_prerequisites(cls, item):
        return organisation_prerequisite(item)

    @classmethod
    def prerequisites_due(cls):
        return UNVERIFIED_ORGANISATION

    @classmethod
    def on_approve(cls, item, actor):
        return (
            OutcomeDefinition(
                key="milestone_approval",
                name=f"{item.application.title} approved",
                field_schema=[
                    {"key": "decision_note", "label": "Decision"},
                    {"key": "production_handoff", "label": "Production credentials"},
                ],
                data={
                    "milestone": item.application.milestone.key,
                    "approved_on": item.decided_at.isoformat(),
                    "decision_note": item.decision_note,
                    "production_handoff": (
                        "Issued by the NHA gateway team. The production client ID "
                        "appears on the Credentials page once it is recorded."
                    ),
                },
            ),
            *wasa_approval_outcomes(item),
        )


class WasaReview(ApplicationFormDefinition):
    key = "abdm_wasa"
    name = "WASA certification"
    form_class = WasaReviewForm
    reuse_scope = FormReuseScope.PRODUCT
    request_label = "WASA review"
    submit_label = "Submit WASA for review"
    submitted_message = "WASA submitted for review."
    approval_notice = (
        "Approval makes this certificate available for the product's milestones "
        "until its stated expiry date."
    )

    @classmethod
    def form_kwargs(cls, item):
        return {"product": item.product}

    @classmethod
    def snapshot_valid_until(cls, form):
        return form.cleaned_data.get("wasa_valid_until")

    @classmethod
    def read_document(cls, field_key, upload):
        return read_wasa_certificate(field_key, upload)

    @classmethod
    def approval_block_reason(cls, item):
        return wasa_approval_block_reason(item)

    @classmethod
    def on_approve(cls, item, actor):
        return wasa_approval_outcomes(item)


class ProductRegistration(ApplicationFormDefinition):
    """Registering or editing a product applies at once; nobody approves it."""

    key = "sandbox_product_registration"
    name = "Product registration"
    form_class = ProductRegistrationForm
    allow_approved_updates = True
    auto_approve = True

    @classmethod
    def on_submit(cls, item, data, actor):
        project_product(
            item,
            actor,
            product_values=ABDM.product_values(data),
            solution_type=data["solution_type"],
            selections=data["applied_milestones"],
        )

    @classmethod
    def on_approve(cls, item, actor):
        ProductWorkspace.objects.filter(
            product=item.product,
            registered_at__isnull=True,
        ).update(registered_at=item.decided_at)
        return ()


class UhiParticipation(ApplicationFormDefinition):
    key = "sandbox_uhi_participation"
    name = "UHI participation"
    form_class = UhiParticipationForm
    auto_approve = True
    allow_approved_updates = True
    request_label = "UHI application"
    submit_label = "Submit UHI application"
    submitted_message = "UHI application recorded."
    approval_notice = (
        "UHI participation is recorded rather than assessed. NHA contacts you "
        "directly about onboarding."
    )

    @classmethod
    def pending_prerequisites(cls, item):
        return organisation_prerequisite(item)

    @classmethod
    def prerequisites_due(cls):
        return UNVERIFIED_ORGANISATION


class UhiApplication(ApplicationDefinition):
    key = "abdm_uhi_participation"
    name = "UHI participation"
    reference_prefix = "UHI"
    forms = (UhiParticipation,)


class SandboxExit(ApplicationDefinition):
    key = "abdm_sandbox_exit"
    name = "ABDM sandbox milestone exit"
    reference_prefix = "EXIT"
    forms = (ExitEvidence,)


class SandboxProduct(ApplicationDefinition):
    key = "abdm_sandbox_product"
    name = "ABDM product registration"
    reference_prefix = "REG"
    forms = (ProductRegistration,)


class WasaCertification(ApplicationDefinition):
    key = "abdm_wasa_review"
    name = "WASA certification review"
    reference_prefix = "WASA"
    forms = (WasaReview,)


class ABDM(ProgramDefinition):
    key = "abdm"
    name = "ABDM Developer Sandbox"
    description = "Build, certify and support your integrations with ABDM in one place."
    short_name = "ABDM"
    brand_line_1 = "Developer"
    brand_line_2 = "Sandbox"
    header_name = "ABDM Sandbox"
    review_heading = "NHA assessment"
    reviewer_name = "NHA reviewer"
    environment_name = "Sandbox environment"
    footer_note = "Synthetic data only"
    docs_url = "https://abdm-docs.dev.eka.care/docs/hiecm/v3"
    logo = "images/abdm-logo.png"
    authority_logo = "images/nha-logo.png"
    authority_name = "National Health Authority"
    product_reference_prefix = "SBX"
    solution_types = dict(ProductRegistrationForm.base_fields["solution_type"].choices)
    organisation_form = OrganisationVerification
    applications = ApplicationSet(
        product=SandboxProduct,
        milestone=SandboxExit,
        certification=WasaCertification,
        overrides={"uhi1": UhiApplication},
    )
    milestones = MILESTONES
    tracks = TRACKS
    sandbox_credentials = ABDMSandboxCredentials
    production_credentials = ABDMProductionCredentials
    handoffs = {"dhis": DHISHandoff}
    reference_environment = ABDMReferenceEnvironment
    signup_organisation_choices = tuple(
        OrganisationForm.base_fields["entity_type"].choices,
    )

    @classmethod
    def certification_context(cls, product):
        return wasa_context(product)

    @classmethod
    def on_product_created(cls, product, actor):
        start_provisioning(product, started_by=actor)

    @classmethod
    def seed_demo(cls, **options):
        from .demo import DemoBuilder  # noqa: PLC0415

        DemoBuilder(options.pop("stdout"), options.pop("style")).handle(**options)
