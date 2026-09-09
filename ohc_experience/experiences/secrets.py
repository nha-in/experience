"""The cipher the stored client secret is held under.

Its own module because both the engine and the provisioning chain need it, and
the chain may not import the engine's credential services.
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def cipher():
    key = settings.EXPERIENCE_CREDENTIAL_KEY
    if not key:
        if not settings.EXPERIENCE_ALLOW_INSECURE_DEMO_KEY:
            msg = "Set EXPERIENCE_CREDENTIAL_KEY to a dedicated Fernet key."
            raise ImproperlyConfigured(msg)
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.SECRET_KEY.encode()).digest(),
        )
    return Fernet(key)
