from ohc_experience.experiences.abdm.definition import ABDMProductionAccess
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.definitions import StatusDefinition
from ohc_experience.experiences.registry import registry

from .forms import ExitEvidenceForm
from .forms import ProductRegistrationForm


class PortalFormDefinition(ApplicationFormDefinition):
    allow_updates = True

    @classmethod
    def availability(cls, context):
        # All mutations pass through the review service, including locks and audit.
        return False, "Use this product's sandbox workspace to update the form."


class ExitEvidence(PortalFormDefinition):
    key = "sandbox_exit_evidence"
    name = "Sandbox exit evidence"
    description = "Testing window, WASA audit and functional testing evidence."
    form_class = ExitEvidenceForm


class ProductRegistration(PortalFormDefinition):
    key = "sandbox_product_registration"
    name = "Product registration"
    description = "Product identity, solution type and applied milestones."
    form_class = ProductRegistrationForm


@registry.register
class SandboxExit(ABDMProductionAccess):
    key = "abdm_sandbox_exit"
    name = "ABDM sandbox milestone exit"
    description = "Request NHA review for an individual compliance milestone."
    reference_prefix = "EXIT"
    forms = (ExitEvidence,)
    actions = ()
    statuses = tuple(
        StatusDefinition(key, label, "")
        for key, label in (
            ("draft", "In progress"),
            ("locked", "Locked"),
            ("under_review", "Under review"),
            ("query_raised", "Query raised"),
            ("approved", "Approved"),
        )
    )

    @classmethod
    def initial_product_outcomes(cls, application, actor):
        return ()


@registry.register
class SandboxProduct(SandboxExit):
    key = "abdm_sandbox_product"
    name = "ABDM product registration"
    reference_prefix = "REG"
    forms = (ProductRegistration,)
