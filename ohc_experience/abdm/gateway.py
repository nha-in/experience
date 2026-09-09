from django.conf import settings

from ohc_experience.experiences.definitions import CredentialDefinition

LOCAL_PORT_PREFIX = "ohc_experience.integrations.local."


class ABDMCredentials(CredentialDefinition):
    name = "Sandbox credentials"
    outcome_type = "sandbox_credentials"
    usage_notice = (
        "Use synthetic data only. Do not send real patient or production data "
        "to the sandbox."
    )
    demo_notice = "These credentials come from the local adapters, not the NHA gateway."
    unavailable_notice = "Sandbox credentials are being set up for this product."
    unavailable_heading = "Provisioning in progress"
    handoff_heading = "Production access"
    handoff_notice = (
        "Production credentials are handled by the gateway team after milestone "
        "approval. The decision record contains your handoff details."
    )

    @classmethod
    def gateway_url(cls):
        return settings.ABDM_GATEWAY_URL

    @classmethod
    def is_demo(cls):
        return settings.INTEGRATION_PORTS["IDP"].startswith(LOCAL_PORT_PREFIX)
