"""
With these settings, tests run faster.
"""

from .base import *  # noqa: F403
from .base import TEMPLATES
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="YnoGnjSGJd5sbzoJofvV156osWXJEvPRJWZuVIusnzxpjytUiS85zrYzcHwZqAHA",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore[index]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "http://media.testserver/"
# CELERY
# ------------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# INTEGRATIONS
# ------------------------------------------------------------------------------
# Pinned, not inherited: .envs/.local/.django may point a port at a real system,
# and a test run must never reach one.
INTEGRATION_PORTS = {
    "IDP": "ohc_experience.integrations.local.LocalIdpAdmin",
    "API_GATEWAY": "ohc_experience.integrations.local.LocalApiGateway",
    "BRIDGE_REGISTRY": "ohc_experience.integrations.local.LocalBridgeRegistry",
    "NOTIFICATION": "ohc_experience.integrations.local.LocalNotificationGateway",
}
# The local adapters stand in for all three systems; these are the names they know.
WSO2_API_NAMES = {"abdm": ("HealthIdAPI", "GatewayAPI")}

# The limits live in the cache, which outlasts a test, so one test's verification
# code would rate-limit the next test's signup with the same address.
ACCOUNT_RATE_LIMITS = False

# Your stuff...
# ------------------------------------------------------------------------------
ABDM_ALLOW_DEMO_CREDENTIALS = True
EXPERIENCE_ALLOW_INSECURE_DEMO_KEY = True
SANDBOX_SIGNUP_CAPTCHA = False
