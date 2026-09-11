from .base import *  # noqa: F403
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import REDIS_AUTH_TOKEN
from .base import REDIS_URL
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = True
ABDM_ALLOW_DEMO_CREDENTIALS = True
EXPERIENCE_ALLOW_INSECURE_DEMO_KEY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="Qd9BQgaZTfBla314VCgXRQACfpWO9rhHHKtFIci0KFDjucdzvrVfHkqCOvrg39la",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = ["localhost", "0.0.0.0", "127.0.0.1"]  # noqa: S104

# CACHES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#caches
# Redis rather than locmem: the local integration adapters keep their state here,
# and the chain parks the Keycloak secret here between two of its steps. Both
# cross process boundaries — web to worker, and one prefork child to the next —
# which per-process memory silently breaks.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "PASSWORD": REDIS_AUTH_TOKEN,
        },
    },
}

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-host
EMAIL_HOST = env("EMAIL_HOST", default="mailtrap-local")
# https://docs.djangoproject.com/en/dev/ref/settings/#email-port
EMAIL_PORT = 3535

# WhiteNoise
# ------------------------------------------------------------------------------
# http://whitenoise.evans.io/en/latest/django.html#using-whitenoise-in-development
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True


# django-debug-toolbar
# ------------------------------------------------------------------------------
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#prerequisites
INSTALLED_APPS += ["debug_toolbar"]
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#middleware
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
# https://django-debug-toolbar.readthedocs.io/en/latest/configuration.html#debug-toolbar-config
DEBUG_TOOLBAR_CONFIG = {
    "DISABLE_PANELS": [
        "debug_toolbar.panels.redirects.RedirectsPanel",
        # Disable profiling panel due to an issue with Python 3.12+:
        # https://github.com/jazzband/django-debug-toolbar/issues/1875
        "debug_toolbar.panels.profiling.ProfilingPanel",
    ],
    "SHOW_TEMPLATE_CONTEXT": True,
}
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#internal-ips
INTERNAL_IPS = ["127.0.0.1", "10.0.2.2"]
if env("USE_DOCKER", default="no") == "yes":
    import socket

    hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS += [".".join([*ip.split(".")[:-1], "1"]) for ip in ips]

# django-extensions
# ------------------------------------------------------------------------------
# https://django-extensions.readthedocs.io/en/latest/installation_instructions.html#configuration
INSTALLED_APPS += ["django_extensions"]
# Celery
# ------------------------------------------------------------------------------

# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-eager-propagates
CELERY_TASK_EAGER_PROPAGATES = True

# MinIO-backed private media storage
# ------------------------------------------------------------------------------
AWS_ACCESS_KEY_ID = env("DJANGO_AWS_ACCESS_KEY_ID", default="ohc-local")
AWS_SECRET_ACCESS_KEY = env(
    "DJANGO_AWS_SECRET_ACCESS_KEY",
    default="ohc-local-development-key",
)
AWS_STORAGE_BUCKET_NAME = env(
    "DJANGO_AWS_STORAGE_BUCKET_NAME",
    default="ohc-experience-media",
)
AWS_S3_ENDPOINT_URL = env(
    "DJANGO_AWS_S3_ENDPOINT_URL",
    default="http://minio:9000",
)
AWS_S3_REGION_NAME = env("DJANGO_AWS_S3_REGION_NAME", default="us-east-1")
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": AWS_ACCESS_KEY_ID,
            "secret_key": AWS_SECRET_ACCESS_KEY,
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "endpoint_url": AWS_S3_ENDPOINT_URL,
            "region_name": AWS_S3_REGION_NAME,
            "addressing_style": "path",
            "default_acl": None,
            "file_overwrite": False,
            "location": "media",
            "querystring_auth": True,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
# Your stuff...
# ------------------------------------------------------------------------------

# Shell development uses disk uploads and console mail without Docker services.
if env.bool("DJANGO_USE_LOCAL_MEDIA", default=env("USE_DOCKER", default="no") != "yes"):
    STORAGES["default"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    }
if env("USE_DOCKER", default="no") != "yes":
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# INTEGRATIONS
# ------------------------------------------------------------------------------
# Named so the local gateway has something to subscribe to offline.
WSO2_API_NAMES = {
    "abdm": tuple(
        env.list("WSO2_SANDBOX_API_NAMES", default=["HealthIdAPI", "GatewayAPI"]),
    ),
}
