from django.conf import settings

from ohc_experience.experiences.definitions import ProductionCredentialDefinition
from ohc_experience.experiences.definitions import SandboxCredentialDefinition

LOCAL_PORT_PREFIX = "ohc_experience.integrations.local."


class ABDMSandboxCredentials(SandboxCredentialDefinition):
    name = "Sandbox credentials"
    outcome_type = "sandbox_credentials"
    usage_notice = (
        "Use synthetic data only. Do not send real patient or production data "
        "to the sandbox."
    )
    demo_notice = "These credentials come from the local adapters, not the NHA gateway."
    unavailable_notice = "Sandbox credentials are being set up for this product."
    unavailable_heading = "Provisioning in progress"

    @classmethod
    def gateway_url(cls):
        return settings.ABDM_GATEWAY_URL

    @classmethod
    def is_demo(cls):
        return settings.INTEGRATION_PORTS["IDP"].startswith(LOCAL_PORT_PREFIX)


class ABDMProductionCredentials(ProductionCredentialDefinition):
    usage_notice = (
        "The NHA gateway team sends the production client secret to you directly. "
        "It is never shown or stored in this portal."
    )
    pending_notice = (
        "Your exit is approved. The NHA gateway team issues production credentials; "
        "your production client ID will appear here once it is issued."
    )
