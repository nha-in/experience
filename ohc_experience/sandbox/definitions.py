from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.registry import registry


class ExitEvidence(ApplicationFormDefinition):
    key = "sandbox_exit_evidence"
    name = "Sandbox exit evidence"


class ProductRegistration(ApplicationFormDefinition):
    key = "sandbox_product_registration"
    name = "Product registration"


@registry.register
class SandboxExit(ApplicationDefinition):
    key = "abdm_sandbox_exit"
    name = "ABDM sandbox milestone exit"
    reference_prefix = "EXIT"
    forms = (ExitEvidence,)


@registry.register
class SandboxProduct(ApplicationDefinition):
    key = "abdm_sandbox_product"
    name = "ABDM product registration"
    reference_prefix = "REG"
    forms = (ProductRegistration,)
