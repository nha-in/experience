# ruff: noqa: ERA001, E501
"""Base settings to build other settings files upon."""

import os
import ssl
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent
# ohc_experience/
APPS_DIR = BASE_DIR / "ohc_experience"
env = environ.Env()

READ_DOT_ENV_FILE = env.bool("DJANGO_READ_DOT_ENV_FILE", default=False)
if READ_DOT_ENV_FILE:
    # OS environment variables take precedence over variables from .env
    env.read_env(str(BASE_DIR / ".env"))

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = env.bool("DJANGO_DEBUG", False)
# Local time zone. Choices are
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# though not all of them may be available with every OS.
# In Windows, this must be set to your system time zone.
TIME_ZONE = "Asia/Kolkata"
# https://docs.djangoproject.com/en/dev/ref/settings/#language-code
LANGUAGE_CODE = "en-us"
# https://docs.djangoproject.com/en/dev/ref/settings/#languages
# from django.utils.translation import gettext_lazy as _
# LANGUAGES = [
#     ('en', _('English')),
#     ('fr-fr', _('French')),
#     ('pt-br', _('Portuguese')),
# ]
# https://docs.djangoproject.com/en/dev/ref/settings/#site-id
SITE_ID = 1
# https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = True
# https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = True
# https://docs.djangoproject.com/en/dev/ref/settings/#locale-paths
LOCALE_PATHS = [str(BASE_DIR / "locale")]

# DATABASES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#databases

if os.getenv("DATABASE_URL", default=None):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env.str("POSTGRES_DB"),
            "USER": env.str("POSTGRES_USER"),
            "PASSWORD": env.str("POSTGRES_PASSWORD"),
            "HOST": env.str("POSTGRES_HOST", default="postgres"),
            "PORT": env.str("POSTGRES_PORT", default="5432"),
        },
    }

DATABASES["default"]["ATOMIC_REQUESTS"] = True
# https://docs.djangoproject.com/en/stable/ref/settings/#std:setting-DEFAULT_AUTO_FIELD

# URLS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = "config.urls"
# https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = "config.wsgi.application"

# APPS
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",  # Handy template tags
    "django.contrib.admin",
    "django.forms",
]

TAILWIND_APP_NAME = "theme"
# Must match the Node build output copied into the production image.
TAILWIND_CSS_PATH = "css/dist/styles.css"

NPM_BIN_PATH = env(
    "NPM_BIN_PATH",
    default="/usr/bin/node",
)

# Build Tailwind with the standalone pytailwindcss binary rather than npm.
# The deploy environment (DigitalOcean's Python buildpack) has no node/npm, and
# django-tailwind only falls back to the binary when theme/static_src/package.json
# is absent -- which it no longer is. See tailwind/management/commands/tailwind.py.
TAILWIND_USE_STANDALONE_BINARY = env.bool(
    "TAILWIND_USE_STANDALONE_BINARY",
    default=True,
)

THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "django_celery_beat",
    "django_htmx",
    "tailwind",
    "theme",
]

LOCAL_APPS = [
    "ohc_experience.users",
    "ohc_experience.organisations",
    "ohc_experience.pages",
    "ohc_experience.support",
    "ohc_experience.events",
    "ohc_experience.experiences",
    "ohc_experience.integrations",
    # Your stuff: custom apps go here
]

EXPERIENCE_IMPLEMENTATIONS = ["ohc_experience.abdm.definitions.ABDM"]
EXPERIENCE_PORTAL = "abdm"
# https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# A separate Fernet key is required in production; local development derives one.
EXPERIENCE_CREDENTIAL_KEY = env(
    "EXPERIENCE_CREDENTIAL_KEY",
    default=env("SANDBOX_CREDENTIAL_KEY", default=""),
)
EXPERIENCE_ALLOW_INSECURE_DEMO_KEY = False
ABDM_CREDENTIAL_PROVIDER = env(
    "ABDM_CREDENTIAL_PROVIDER",
    default=env("SANDBOX_CREDENTIAL_PROVIDER", default=""),
)
ABDM_ALLOW_DEMO_CREDENTIALS = False
SANDBOX_SIGNUP_CAPTCHA = True
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", default="")
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="")
ABDM_GATEWAY_URL = env(
    "ABDM_GATEWAY_URL",
    default=env("SANDBOX_GATEWAY_URL", default="https://dev.abdm.gov.in/gateway"),
)

# Legacy DHIS handoff protocol. Keys must be supplied through deployment secrets.
ABDM_DHIS_URL = env("ABDM_DHIS_URL", default="https://dhis.abdm.gov.in/DHIS/")
ABDM_DHIS_JWT_SECRET = env("ABDM_DHIS_JWT_SECRET", default="")
ABDM_DHIS_AES_KEY = env("ABDM_DHIS_AES_KEY", default="")
ABDM_DHIS_AES_IV = env("ABDM_DHIS_AES_IV", default="")

# Server-only Local Government Directory lookup for organisation addresses.
LGD_API_URL = env(
    "LGD_API_URL",
    default="https://apissbx.abdm.gov.in/global/api/v3/internal/lgd",
)
LGD_API_KEY = env("LGD_API_KEY", default="")
LGD_API_TIMEOUT = env.float("LGD_API_TIMEOUT", default=5.0)
LGD_CACHE_TTL = env.int("LGD_CACHE_TTL", default=3600)

# Reads an uploaded WASA certificate to propose the audit fields. Blanking the
# model switches the hook off and the fields are typed in as before.


def _tuning(name, cast, default):
    """A deployment variable left blank means "leave this at the default"."""
    value = env.str(name, default="").strip()
    try:
        return cast(value)
    except ValueError:
        return default


WASA_EXTRACTION_MODEL = env(
    "WASA_EXTRACTION_MODEL",
    default="bedrock/openai.gpt-5.6-luna",
)
WASA_EXTRACTION_TIMEOUT = _tuning("WASA_EXTRACTION_TIMEOUT", float, 45.0)
WASA_EXTRACTION_MAX_TOKENS = _tuning("WASA_EXTRACTION_MAX_TOKENS", int, 512)
WASA_EXTRACTION_CACHE_TTL = _tuning("WASA_EXTRACTION_CACHE_TTL", int, 3600)
# Pages are rendered to images at this resolution. Higher reads small print more
# reliably and costs more per page.
WASA_EXTRACTION_DPI = _tuning("WASA_EXTRACTION_DPI", int, 150)

# Bedrock's own principal, deliberately apart from the AWS_* settings that carry
# the media bucket's credentials: reading a document must not borrow the rights
# to the uploads. Leave these blank to use the host's instance role instead.
BEDROCK_REGION_NAME = env("BEDROCK_REGION_NAME", default="")
BEDROCK_ACCESS_KEY_ID = env("BEDROCK_ACCESS_KEY_ID", default="")
BEDROCK_SECRET_ACCESS_KEY = env("BEDROCK_SECRET_ACCESS_KEY", default="")
# Each account may have this many documents read an hour; a hook pays per call.
EXPERIENCE_DOCUMENT_READ_HOURLY_LIMIT = _tuning(
    "EXPERIENCE_DOCUMENT_READ_HOURLY_LIMIT",
    int,
    20,
)

# MIGRATIONS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#migration-modules
MIGRATION_MODULES = {"sites": "ohc_experience.contrib.sites.migrations"}

# AUTHENTICATION
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#authentication-backends
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-user-model
AUTH_USER_MODEL = "users.User"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-redirect-url
LOGIN_REDIRECT_URL = "users:redirect"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-url
LOGIN_URL = "account_login"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = [
    # https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    # Verify-only, for accounts imported from the legacy sandbox (Spring bcrypt,
    # stored as "bcrypt$$2a$…"). Argon2 above re-hashes them on first login.
    "django.contrib.auth.hashers.BCryptPasswordHasher",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        # The signup card asks for "Min 12 characters"; hold the form to it.
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {
        "NAME": "ohc_experience.users.password_validation.LetterNumberAndSpecialCharacterValidator",
    },
]

# MIDDLEWARE
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#middleware
MIDDLEWARE = [
    "ohc_experience.core.health.PingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

# STATIC
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = str(BASE_DIR / "staticfiles")
# https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = "/static/"
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = [str(APPS_DIR / "static")]
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = str(APPS_DIR / "media")
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "/media/"

# TEMPLATES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # https://docs.djangoproject.com/en/dev/ref/settings/#dirs
        "DIRS": [str(APPS_DIR / "templates")],
        # https://docs.djangoproject.com/en/dev/ref/settings/#app-dirs
        "APP_DIRS": True,
        "OPTIONS": {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
                "ohc_experience.users.context_processors.allauth_settings",
                "ohc_experience.users.context_processors.nha_team",
                "ohc_experience.organisations.context_processors.current_organisation",
                "ohc_experience.experiences.context_processors.experience_program",
            ],
        },
    },
]

# https://docs.djangoproject.com/en/dev/ref/settings/#form-renderer
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# FIXTURES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#fixture-dirs
FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

# SECURITY
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-httponly
SESSION_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-httponly
CSRF_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#x-frame-options
X_FRAME_OPTIONS = "DENY"

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-timeout
EMAIL_TIMEOUT = env.float("DJANGO_EMAIL_TIMEOUT", default=5)

# Internal Global Email API. The URL includes the full /email/send path.
# Empty defaults prevent accidental use of an unapproved gateway/template.
ANYMAIL = {
    "GLOBAL_EMAIL_API_URL": env("GLOBAL_EMAIL_API_URL", default=""),
    "GLOBAL_EMAIL_TEMPLATE_ID": env("GLOBAL_EMAIL_TEMPLATE_ID", default=""),
    "GLOBAL_EMAIL_ORIGIN": env("GLOBAL_EMAIL_ORIGIN", default="abha"),
    "GLOBAL_EMAIL_SENDER": env("GLOBAL_EMAIL_SENDER", default="NHASMS"),
}
GLOBAL_EMAIL_TEMPLATE_IDS = env.json("GLOBAL_EMAIL_TEMPLATE_IDS", default={})

# SUPPORT TICKET EMAIL THREADING
# ------------------------------------------------------------------------------
SUPPORT_INBOX_EMAIL = env("SUPPORT_INBOX_EMAIL", default="support@ohc.network")
SUPPORT_EMAIL_DOMAIN = env("SUPPORT_EMAIL_DOMAIN", default="sandbox.aws.ohc.network")
# Absolute base for links in emails (no request is available there).
# The portless proxy gives each git worktree a different origin and puts it in
# PORTLESS_URL. Use it, so a link points to the worktree that sent the mail.
SITE_BASE_URL = env(
    "SITE_BASE_URL",
    default=env("PORTLESS_URL", default="http://localhost:8010"),
)

# ADMIN
# ------------------------------------------------------------------------------
# Django Admin URL.
ADMIN_URL = "admin/"
# https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMINS = ['"OHCNF" <ohcnf@ohc.network>']
# https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS
# https://cookiecutter-django.readthedocs.io/en/latest/settings.html#other-environment-settings
# Force the `admin` sign in process to go through the `django-allauth` workflow
DJANGO_ADMIN_FORCE_ALLAUTH = env.bool("DJANGO_ADMIN_FORCE_ALLAUTH", default=False)

# LOGGING
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#logging
# See https://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

REDIS_URL = env("REDIS_URL", default="redis://redis:6379/0")
# Read literally: django-environ interprets a leading "$" as a variable reference.
REDIS_AUTH_TOKEN = os.environ.get("REDIS_AUTH_TOKEN") or None
REDIS_SSL = REDIS_URL.startswith("rediss://")

# Celery
# ------------------------------------------------------------------------------
if USE_TZ:
    # https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-timezone
    CELERY_TIMEZONE = TIME_ZONE
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-broker_url
CELERY_BROKER_URL = REDIS_URL
CELERY_BROKER_PASSWORD = REDIS_AUTH_TOKEN
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#redis-backend-use-ssl
CELERY_BROKER_USE_SSL = (
    {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_check_hostname": True}
    if REDIS_SSL
    else None
)
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-result_backend
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_REDIS_PASSWORD = REDIS_AUTH_TOKEN
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#redis-backend-use-ssl
CELERY_REDIS_BACKEND_USE_SSL = CELERY_BROKER_USE_SSL
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-extended
CELERY_RESULT_EXTENDED = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-backend-always-retry
# https://github.com/celery/celery/pull/6122
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-backend-max-retries
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-accept_content
CELERY_ACCEPT_CONTENT = ["json"]
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-task_serializer
CELERY_TASK_SERIALIZER = "json"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-result_serializer
CELERY_RESULT_SERIALIZER = "json"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-time-limit
# TODO: set to whatever value is adequate in your circumstances
CELERY_TASK_TIME_LIMIT = 5 * 60
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-soft-time-limit
# TODO: set to whatever value is adequate in your circumstances
CELERY_TASK_SOFT_TIME_LIMIT = 60
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#beat-scheduler
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_SCHEDULE = {
    "sandbox-callback-health": {
        "task": "ohc_experience.experiences.tasks.monitor_callbacks",
        "schedule": 900.0,
    },
    "sandbox-notifications": {
        "task": "ohc_experience.experiences.tasks.deliver_notifications",
        "schedule": 60.0,
    },
    "sandbox-event-reminders": {
        "task": "ohc_experience.experiences.tasks.remind_event_registrations",
        "schedule": 3600.0,
    },
}
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-send-task-events
CELERY_WORKER_SEND_TASK_EVENTS = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_send_sent_event
CELERY_TASK_SEND_SENT_EVENT = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-hijack-root-logger
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
# django-allauth
# ------------------------------------------------------------------------------
ACCOUNT_ALLOW_REGISTRATION = env.bool("DJANGO_ACCOUNT_ALLOW_REGISTRATION", True)
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_LOGIN_METHODS = {"email"}
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_SIGNUP_REDIRECT_URL = "users:redirect"
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
# Verification codes, not links, sent by email and SMS through the notification gateway.
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_FORMAT = {
    "numeric": True,
    "length": 6,
    "dashed": False,
}
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND = 3
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_CHANGE = True
ACCOUNT_PHONE_VERIFICATION_CODE_FORMAT = ACCOUNT_EMAIL_VERIFICATION_BY_CODE_FORMAT
ACCOUNT_PHONE_VERIFICATION_SUPPORTS_RESEND = 3
ACCOUNT_PHONE_VERIFICATION_SUPPORTS_CHANGE = True
# How long before a new code can be asked for, as on the legacy portal.
VERIFICATION_RESEND_AFTER_SECONDS = 90
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_ADAPTER = "ohc_experience.users.adapters.AccountAdapter"
# https://docs.allauth.org/en/latest/account/forms.html
ACCOUNT_FORMS = {
    "login": "ohc_experience.users.forms.UserLoginForm",
    "signup": "ohc_experience.users.forms.UserSignupForm",
    "reset_password": "ohc_experience.users.forms.UserResetPasswordForm",
    "reset_password_from_key": "ohc_experience.users.forms.UserResetPasswordKeyForm",
    "change_password": "ohc_experience.users.forms.UserChangePasswordForm",
    "set_password": "ohc_experience.users.forms.UserSetPasswordForm",
    "change_email": "ohc_experience.users.forms.UserChangeEmailForm",
    "confirm_email_verification_code": (
        "ohc_experience.users.forms.UserConfirmEmailVerificationCodeForm"
    ),
    "verify_phone": "ohc_experience.users.forms.UserVerifyPhoneForm",
    "change_phone": "ohc_experience.users.forms.UserChangePhoneForm",
}
# https://docs.allauth.org/en/latest/socialaccount/configuration.html
SOCIALACCOUNT_ADAPTER = "ohc_experience.users.adapters.SocialAccountAdapter"
# https://docs.allauth.org/en/latest/socialaccount/configuration.html
SOCIALACCOUNT_FORMS = {"signup": "ohc_experience.users.forms.UserSocialSignupForm"}
# django-compressor
# ------------------------------------------------------------------------------
# https://django-compressor.readthedocs.io/en/latest/quickstart/#installation
INSTALLED_APPS += ["compressor"]
STATICFILES_FINDERS += ["compressor.finders.CompressorFinder"]

# Your stuff...
# ------------------------------------------------------------------------------

# INTEGRATIONS
# ------------------------------------------------------------------------------
# Each port resolves to a real adapter or a local stand-in. The defaults are the
# local ones, so a laptop with no VPN runs the whole portal; deployments override.
INTEGRATION_PORTS = {
    "IDP": env.str(
        "INTEGRATION_IDP",
        default="ohc_experience.integrations.local.LocalIdpAdmin",
    ),
    "API_GATEWAY": env.str(
        "INTEGRATION_API_GATEWAY",
        default="ohc_experience.integrations.local.LocalApiGateway",
    ),
    "BRIDGE_REGISTRY": env.str(
        "INTEGRATION_BRIDGE_REGISTRY",
        default="ohc_experience.integrations.local.LocalBridgeRegistry",
    ),
    "NOTIFICATION": "ohc_experience.integrations.local.LocalNotificationGateway",
}

# NOTIFICATION GATEWAY
# ------------------------------------------------------------------------------
# Reachable only from inside the ABDM VPC.
NOTIFICATION_APP_BASE_URL = env.str(
    "NOTIFICATION_APP_BASE_URL",
    default="https://notification-app.invalid",
)
NOTIFICATION_DB_BASE_URL = env.str(
    "NOTIFICATION_DB_BASE_URL",
    default="https://notification-db.invalid",
)

# KEYCLOAK
# ------------------------------------------------------------------------------
# Legacy's admin sign-in: a master-realm client and user, with a password grant.
KEYCLOAK_BASE_URL = env.str("KEYCLOAK_BASE_URL", default="http://keycloak:8080")
KEYCLOAK_REALM = env.str("KEYCLOAK_REALM", default="abdm-sandbox")
KEYCLOAK_CLIENT_ID = env.str("KEYCLOAK_CLIENT_ID", default="admin-cli")
KEYCLOAK_CLIENT_SECRET = env.str("KEYCLOAK_CLIENT_SECRET", default="")
KEYCLOAK_USERNAME = env.str("KEYCLOAK_USERNAME", default="")
KEYCLOAK_PASSWORD = env.str("KEYCLOAK_PASSWORD", default="")
KEYCLOAK_API_KEY = env.str("KEYCLOAK_API_KEY", default="")
# Role NAMES per program, never realm UUIDs. The default is the set legacy gave
# every sandbox client.
KEYCLOAK_ROLE_NAMES = {
    "abdm": tuple(
        env.list(
            "KEYCLOAK_SANDBOX_ROLE_NAMES",
            default=[
                "bridge",
                "HIU_PAYER",
                "DIGI_DOCTOR",
                "healthId",
                "health_locker",
                "hip",
                "HIP_PAYER",
                "hiu",
                "hfr",
                "offline_access",
                "phr",
                "OIDC",
                "HidAbhaSearch",
                "hp_id",
            ],
        ),
    ),
}

# WSO2
# ------------------------------------------------------------------------------
WSO2_BASE_URL = env.str("WSO2_BASE_URL", default="https://wso2.invalid")
WSO2_DEVPORTAL_PATH = env.str("WSO2_DEVPORTAL_PATH", default="/api/am/devportal/v2.1")
WSO2_TOKEN_PATH = env.str("WSO2_TOKEN_PATH", default="/oauth2/token")
WSO2_CLIENT_ID = env.str("WSO2_CLIENT_ID", default="")
WSO2_CLIENT_SECRET = env.str("WSO2_CLIENT_SECRET", default="")
WSO2_USERNAME = env.str("WSO2_USERNAME", default="")
WSO2_PASSWORD = env.str("WSO2_PASSWORD", default="")
WSO2_GRANT_TYPE = env.str("WSO2_GRANT_TYPE", default="password")
WSO2_SCOPES = tuple(
    env.list(
        "WSO2_SCOPES",
        default=["apim:subscribe", "apim:app_manage", "apim:sub_manage"],
    ),
)
WSO2_THROTTLING_POLICY = env.str("WSO2_THROTTLING_POLICY", default="Unlimited")
WSO2_TOKEN_TYPE = env.str("WSO2_TOKEN_TYPE", default="JWT")
WSO2_KEY_MANAGER = env.str("WSO2_KEY_MANAGER", default="Resident Key Manager")
WSO2_KEY_TYPE = env.str("WSO2_KEY_TYPE", default="PRODUCTION")
WSO2_READ_TIMEOUT_SECONDS = env.float("WSO2_READ_TIMEOUT_SECONDS", default=15.0)
# API ids, as legacy subscribed. No default: a wrong or empty guess would fail
# silently at provisioning time.
WSO2_API_IDS = {
    "abdm": tuple(env.list("WSO2_SANDBOX_API_IDS", default=[])),
}

# How long a secret parked for `map_keys` stays readable.
SECRET_REF_TTL_SECONDS = env.int("SECRET_REF_TTL_SECONDS", default=900)

# HIE-CM
# ------------------------------------------------------------------------------
# Internal base URL only — the external rewrite is owned by infrastructure.
HIECM_BASE_URL = env.str("HIECM_BASE_URL", default="https://hiecm.invalid")
HIECM_API_PATH = env.str("HIECM_API_PATH", default="/api/v3")
HIECM_CM_ID = env.str("HIECM_CM_ID", default="sbx")
# Where HIE-CM delivers an integrator's gateway callbacks. `.invalid` by default,
# so an unconfigured deployment cannot quietly publish somebody else's host.
HIECM_BRIDGE_CALLBACK_BASE_URL = env.str(
    "HIECM_BRIDGE_CALLBACK_BASE_URL",
    default="https://bridge.invalid",
)

# PROVISIONING CHAIN
# ------------------------------------------------------------------------------
# ~30 minutes across five attempts (120s doubling, capped at 15m). The ledger,
# not this policy, is what makes a retry safe.
PROVISIONING_MAX_ATTEMPTS = env.int("PROVISIONING_MAX_ATTEMPTS", default=5)
PROVISIONING_RETRY_BACKOFF_SECONDS = env.int(
    "PROVISIONING_RETRY_BACKOFF_SECONDS",
    default=120,
)
PROVISIONING_RETRY_BACKOFF_MAX_SECONDS = env.int(
    "PROVISIONING_RETRY_BACKOFF_MAX_SECONDS",
    default=900,
)
PROVISIONING_DETAIL_MAX_CHARS = 500

# LOCAL ADAPTERS
# ------------------------------------------------------------------------------
# What the local realm contains, so `LocalIdpAdmin` 404s an unknown role name the
# way a real Keycloak does. Mirrors compose/local/keycloak/, so a role in
# KEYCLOAK_ROLE_NAMES but not here fails offline rather than on first contact.
LOCAL_KEYCLOAK_REALM_ROLES = env.list(
    "LOCAL_KEYCLOAK_REALM_ROLES",
    default=[
        "bridge",
        "hip",
        "hiu",
        "healthId",
        "health_locker",
        "offline_access",
        "phr",
        "hfr",
        "hp_id",
        "OIDC",
        "HidAbhaSearch",
        "DIGI_DOCTOR",
        "HIP_PAYER",
        "HIU_PAYER",
    ],
)
LOCAL_BRIDGE_ACTIVATION_DELAY_SECONDS = env.float(
    "LOCAL_BRIDGE_ACTIVATION_DELAY_SECONDS",
    default=5.0,
)
