import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from ohc_experience.experiences.definitions import CredentialDefinition


class ABDMCredentials(CredentialDefinition):
    name = "Sandbox credentials"
    outcome_type = "sandbox_credentials"
    usage_notice = (
        "Use synthetic data only. Do not send real patient or production data "
        "to the sandbox."
    )
    demo_notice = (
        "Demo credentials for this local portal. "
        "These are not provisioned on the NHA gateway."
    )
    unavailable_notice = (
        "Sandbox credentials are issued once your organisation is verified."
    )
    unavailable_heading = "Organisation verification pending"
    handoff_heading = "Production access"
    handoff_notice = (
        "Production credentials are handled by the gateway team after milestone "
        "approval. The decision record contains your handoff details."
    )

    @classmethod
    def is_demo(cls):
        return (
            settings.ABDM_ALLOW_DEMO_CREDENTIALS
            and not settings.ABDM_CREDENTIAL_PROVIDER
        )

    @classmethod
    def eligibility_error(cls, product):
        return (
            ""
            if product.organisation.is_verified
            else "Credentials require a verified organisation."
        )

    @classmethod
    def provision(cls, product, operation):
        if settings.ABDM_CREDENTIAL_PROVIDER:
            return import_string(settings.ABDM_CREDENTIAL_PROVIDER)(
                product=product, operation=operation,
            )
        if not settings.ABDM_ALLOW_DEMO_CREDENTIALS:
            msg = "Configure an ABDM gateway credential provider before issuance."
            raise ImproperlyConfigured(msg)
        return {
            "client_id": f"demo_{product.workspace.reference.lower()}",
            "client_secret": secrets.token_urlsafe(32),
            "gateway_url": settings.ABDM_GATEWAY_URL,
        }
