from django.utils import timezone

from ohc_experience.experiences.credentials import issue_credentials
from ohc_experience.experiences.credentials import suspend_organisation_credentials
from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.definitions import OutcomeDefinition
from ohc_experience.experiences.definitions import ProgramDefinition
from ohc_experience.experiences.models import FormReuseScope
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.workflows import project_product

from .catalog import MILESTONES
from .catalog import TRACKS
from .forms import ExitEvidenceForm
from .forms import OrganisationForm
from .forms import ProductRegistrationForm
from .gateway import ABDMCredentials


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
            "entity_type": item.form.metadata.get("entity_type", "private_company"),
        }

    @classmethod
    def on_submit(cls, item, data, actor):
        org = item.organisation
        org.name = org.legal_name = data["name"]
        org.website, org.state, org.city = (
            data["website"],
            data["state"],
            data["district"],
        )
        org.onboarded_at = org.onboarded_at or timezone.now()
        org.verification_status = "pending"
        org.verified_at = None
        org.save()
        suspend_organisation_credentials(org, actor)

    @classmethod
    def on_approve(cls, item, actor):
        item.organisation.set_verification("verified")
        for product in item.organisation.products.filter(
            workspace__experience_type=ABDM.key,
        ):
            issue_credentials(product, actor)
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
        "Production credentials are issued separately by the gateway team."
    )

    @classmethod
    def submission_block_reason(cls, item):
        if (
            not item.organisation.is_verified
            or item.product.workspace.registration_status != "registered"
        ):
            return (
                "Organisation verification and product registration must be "
                "approved before requesting exit."
            )
        return ""

    @classmethod
    def on_approve(cls, item, actor):
        return (
            OutcomeDefinition(
                key="milestone_approval",
                name=f"{item.application.title} approved",
                field_schema=[
                    {"key": "decision_note", "label": "Decision"},
                    {"key": "production_handoff", "label": "Production access"},
                ],
                data={
                    "milestone": item.application.milestone.key,
                    "approved_on": item.decided_at.isoformat(),
                    "decision_note": item.decision_note,
                    "production_handoff": (
                        "Production credentials are issued separately "
                        "by the gateway team."
                    ),
                },
            ),
        )


class ProductRegistration(ApplicationFormDefinition):
    key = "sandbox_product_registration"
    name = "Product registration"
    form_class = ProductRegistrationForm
    allow_approved_updates = True

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
        ProductWorkspace.objects.filter(product=item.product).update(
            registration_status="registered",
            registered_at=item.decided_at,
        )
        return ()

    @classmethod
    def on_send_back(cls, item, actor):
        ProductWorkspace.objects.filter(product=item.product).update(
            registration_status="sent_back",
        )


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
    product_types = dict(ProductRegistrationForm.base_fields["category"].choices)
    solution_types = dict(ProductRegistrationForm.base_fields["solution_type"].choices)
    organisation_form = OrganisationVerification
    product_application = SandboxProduct
    milestone_application = SandboxExit
    milestones = MILESTONES
    tracks = TRACKS
    credentials = ABDMCredentials
    signup_organisation_choices = (
        ("private_company", "Company"),
        ("government", "Government"),
        ("sole_proprietor", "Sole proprietor"),
    )

    @classmethod
    def product_values(cls, data):
        return {
            "name": data["name"],
            "description": data["description"],
            "product_type": data["category"],
        }

    @classmethod
    def signup_metadata(cls, data):
        return {"entity_type": data.get("organisation_type") or "private_company"}

    @classmethod
    def on_product_created(cls, product, actor):
        if product.organisation.is_verified:
            issue_credentials(product, actor)

    @classmethod
    def seed_demo(cls, **options):
        from .demo import DemoBuilder  # noqa: PLC0415

        DemoBuilder(options.pop("stdout"), options.pop("style")).handle(**options)
