"""Email transport selection and explicit gateway template mapping."""

from anymail.exceptions import AnymailConfigurationError
from anymail.utils import UNSET
from anymail.utils import get_anymail_setting
from anymail.utils import last
from django.conf import settings

GLOBAL_EMAIL_BACKEND = "ohc_experience.core.mail.backends.GlobalEmailBackend"
QUEUED_GLOBAL_EMAIL_BACKEND = "ohc_experience.core.mail.queue.QueuedGlobalEmailBackend"


def get_delivery_backend():
    """The outbox worker must deliver directly, never enqueue another copy."""
    if settings.EMAIL_BACKEND == QUEUED_GLOBAL_EMAIL_BACKEND:
        return GLOBAL_EMAIL_BACKEND
    return settings.EMAIL_BACKEND


def apply_gateway_template(message, key):
    """Resolve an approved ID once, preserving Anymail's default precedence."""
    if get_delivery_backend() != GLOBAL_EMAIL_BACKEND:
        return
    explicit = getattr(message, "template_id", UNSET)
    templates = getattr(settings, "GLOBAL_EMAIL_TEMPLATE_IDS", {})
    if not isinstance(templates, dict):
        error = "Configure GLOBAL_EMAIL_TEMPLATE_IDS as an object of template IDs."
        raise AnymailConfigurationError(error)
    if explicit is not UNSET and explicit is not None:
        template_id = explicit
    elif key in templates:
        # An explicit empty mapping must fail, never fall through to another
        # purpose's template or a general fallback.
        template_id = templates[key]
    else:
        defaults = get_anymail_setting("send_defaults", default={}).copy()
        gateway_defaults = get_anymail_setting(
            "send_defaults",
            esp_name="Global Email",
            default=None,
        )
        if gateway_defaults is not None:
            defaults.update(gateway_defaults)
        template_id = last(defaults.get("template_id", UNSET), explicit)
        if template_id is UNSET:
            template_id = get_anymail_setting(
                "template_id",
                esp_name="Global Email",
                default=None,
            )
    if (
        not isinstance(template_id, str)
        or not template_id.strip()
        or template_id != template_id.strip()
        or any(ord(character) < 32 for character in template_id)  # noqa: PLR2004
    ):
        error = f"Configure a valid Global Email template ID for {key}."
        raise AnymailConfigurationError(error)
    message.template_id = template_id
