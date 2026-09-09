# ABDM Experience — Deployment Environment Variables

Prepared: 10 September 2026. Reviewed against source revision: `65645e8`.

This document lists the application and deployment environment variables supported by the current repository. It contains variable names, public code defaults, and configuration guidance; it contains no deployed credentials or secret values.

**Required** means a value must be supplied to start the relevant process or operate the named feature. **Optional** means the code supplies a default. An application can start while a feature remains unconfigured, so startup alone does not establish deployment readiness. Defaults referring to a sandbox or local service must be checked against the intended environment.

For the **application and database**, supply these values:

| Variable | Requirement / value |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | Production image default: `config.settings.production`, used by web, Celery worker, Celery beat, Flower, and management commands. Set it explicitly when running production processes outside that image; management commands and Celery otherwise default to local settings. `config.settings.build` is only for offline asset creation and verification, never runtime serving. |
| `DJANGO_SECRET_KEY` | **Required.** Strong, persistent Django secret. |
| `DJANGO_ALLOWED_HOSTS` | Set comma-separated application hostnames without schemes or paths. Code default: `*`. |
| `DJANGO_ADMIN_URL` | **Required.** Admin path, e.g. `admin/`. |
| `DATABASE_URL` | PostgreSQL connection URL, e.g. `postgres://USER:PASSWORD@HOST:5432/DB`. Takes precedence over individual database settings. |
| `POSTGRES_HOST` | Database hostname. Django default: `postgres`; **must be explicitly supplied to the stock Docker entrypoint**. |
| `POSTGRES_PORT` | Database port. Django default: `5432`; **must be explicitly supplied to the stock Docker entrypoint**. |
| `POSTGRES_USER` | Database username. **Must be defined for the stock Docker entrypoint**, including when using `DATABASE_URL`. |
| `POSTGRES_PASSWORD` | **Required** with individual database settings or the bundled PostgreSQL service. |
| `POSTGRES_DB` | **Required** with individual database settings or the bundled PostgreSQL service. |
| `REDIS_URL` | Shared Redis connection for cache, Celery broker/results, and temporary credential references. Django default: `redis://redis:6379/0`; **must be explicitly supplied when running Flower**. |
| `REDIS_AUTH_TOKEN` | Optional raw Redis password for Django cache, Celery broker/results, and Flower. Leave credentials out of `REDIS_URL` when using this variable; passwords already embedded in the URL take precedence. Use `rediss://` for TLS. See [Redis authentication](abdm_sandbox.md#redis-authentication) for ECS configuration. |
| `SENTRY_DSN` | **Must be defined** in production. Supply a valid DSN, or an empty string to disable reporting. |
| `EXPERIENCE_CREDENTIAL_KEY` | **Required for credential encryption.** Dedicated valid Fernet key, separate from `DJANGO_SECRET_KEY`. Preserve it across deployments so existing credentials remain decryptable. |

**Database configuration has two layers:** Django accepts `DATABASE_URL` or the individual PostgreSQL variables, but the stock container entrypoint independently reads `POSTGRES_HOST`, `POSTGRES_PORT`, and `POSTGRES_USER`. Supply those three even with a managed database URL, using values matching that database. When using bundled PostgreSQL and its maintenance scripts, supply all five PostgreSQL variables. The bundled backup/restore scripts require a dedicated user and reject `POSTGRES_USER=postgres`.

Source: `config/settings/base.py`, `config/settings/production.py`, `config/celery_app.py`, `manage.py`, `compose/production/django/entrypoint`, `ohc_experience/experiences/secrets.py`.

For **private upload storage**, production uses S3 or compatible object storage:

| Variable | Requirement / default |
| --- | --- |
| `DJANGO_AWS_ACCESS_KEY_ID` | **Required.** Storage access key. |
| `DJANGO_AWS_SECRET_ACCESS_KEY` | **Required.** Storage secret key. |
| `DJANGO_AWS_STORAGE_BUCKET_NAME` | **Required.** Private upload bucket name. |
| `DJANGO_AWS_S3_REGION_NAME` | Set the bucket region. Default: unset. |
| `DJANGO_AWS_S3_ENDPOINT_URL` | Set for an S3-compatible service; otherwise leave unset. |
| `DJANGO_AWS_S3_CUSTOM_DOMAIN` | Optional domain used to construct `MEDIA_URL`. Default: unset. |
| `DJANGO_AWS_S3_MAX_MEMORY_SIZE` | Optional. Default: `100000000` bytes. |

The bundled AWS backup scripts reuse the three required `DJANGO_AWS_*` credentials/bucket values. They derive `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_STORAGE_BUCKET_NAME` internally; duplicate environment entries are unnecessary for that workflow. Those scripts do not automatically adopt `DJANGO_AWS_S3_ENDPOINT_URL` or `DJANGO_AWS_S3_REGION_NAME` for AWS CLI operations.

Source: `config/settings/production.py`, `compose/production/aws/maintenance/upload`, `compose/production/aws/maintenance/download`.

For **signup and organisation address lookup**, configure:

| Variable | Requirement / default |
| --- | --- |
| `TURNSTILE_SITE_KEY` | **Required for production signup.** Turnstile site key registered for the deployed hostname. Default: empty. |
| `TURNSTILE_SECRET_KEY` | **Required for production signup.** Turnstile secret key. Default: empty. |
| `LGD_API_KEY` | **Required for address lookup.** Server-side LGD API key. Default: empty. |
| `LGD_API_URL` | Default: `https://apissbx.abdm.gov.in/global/api/v3/internal/lgd`. Override with the approved HTTPS LGD base URL for the deployment. |
| `LGD_API_TIMEOUT` | Default: `5` seconds. Allowed: greater than `0`, up to `30`. |
| `LGD_CACHE_TTL` | Default: `3600` seconds. Allowed: `0–86400`; `0` disables caching. |

Missing Turnstile configuration blocks production signup. Organisation address validation calls the LGD API directly and requires its URL and key. There is no configurable provider or local fallback in the current lookup implementation.

Source: `config/settings/base.py`, `ohc_experience/users/captcha.py`, `ohc_experience/organisations/lgd.py`.

For **email delivery**, production uses the Global Email API through a durable notification outbox. Configure the gateway and template settings on both the web process and Celery worker:

| Variable | Requirement / default |
| --- | --- |
| `GLOBAL_EMAIL_API_URL` | **Required for delivery.** Complete reachable send endpoint, including `/internal/v3/notification/email/send`. Default: empty. |
| `GLOBAL_EMAIL_TEMPLATE_IDS` | JSON object mapping email purposes to approved template ID strings. Default: `{}`. |
| `GLOBAL_EMAIL_TEMPLATE_ID` | Optional approved fallback for unmapped purposes. Default: empty. Every message needs an explicit, mapped, or fallback template ID. |
| `GLOBAL_EMAIL_ORIGIN` | Default: `abha`. |
| `GLOBAL_EMAIL_SENDER` | Default: `NHASMS`. |
| `DJANGO_EMAIL_BACKEND` | Production default: `ohc_experience.core.mail.queue.QueuedGlobalEmailBackend`. |
| `DJANGO_EMAIL_TIMEOUT` | Default: `5` seconds. |
| `DJANGO_DEFAULT_FROM_EMAIL` | Default: `OHC Experience <noreply@experience.ohc.network>`. |
| `DJANGO_SERVER_EMAIL` | Defaults to `DJANGO_DEFAULT_FROM_EMAIL`. |
| `DJANGO_EMAIL_SUBJECT_PREFIX` | Default: `[OHC Experience] `, including a trailing space. |

The main purpose keys for `GLOBAL_EMAIL_TEMPLATE_IDS` are:

| Purpose key | Message |
| --- | --- |
| `notification` | Workflow, review, support, and event notifications. |
| `organisation_invitation` | Organisation membership invitation. |
| `account/email/email_confirmation_signup` | Signup verification. |
| `account/email/email_confirmation` | Existing-account email verification. |
| `account/email/password_reset_key` | Password reset link. |

Other account notices may require additional allauth template-prefix keys. Obtain approved template IDs for each purpose or an approved fallback; the code does not assume sample IDs are valid. The Global Email integration does not require NIC SMTP credentials. Its gateway sender determines the actual From address. Celery worker and beat are needed for automatic delivery.

Source: `config/settings/base.py`, `config/settings/production.py`, `docs/global_email.md`.

For **live credential provisioning**, explicitly select all three real adapters:

```dotenv
INTEGRATION_IDP=ohc_experience.integrations.keycloak.adapter.KeycloakIdpAdmin
INTEGRATION_API_GATEWAY=ohc_experience.integrations.wso2.adapter.Wso2ApiGateway
INTEGRATION_BRIDGE_REGISTRY=ohc_experience.integrations.hiecm.adapter.HiecmBridgeRegistry
```

**Production settings otherwise retain local stand-ins.** The defaults are:

| Variable | Default |
| --- | --- |
| `INTEGRATION_IDP` | `ohc_experience.integrations.local.LocalIdpAdmin` |
| `INTEGRATION_API_GATEWAY` | `ohc_experience.integrations.local.LocalApiGateway` |
| `INTEGRATION_BRIDGE_REGISTRY` | `ohc_experience.integrations.local.LocalBridgeRegistry` |

For the **Keycloak adapter**, configure:

| Variable | Requirement / default |
| --- | --- |
| `KEYCLOAK_BASE_URL` | Set the actual service URL. Default: `http://keycloak:8080`. |
| `KEYCLOAK_REALM` | Set the target realm. Default: `abdm-sandbox`. |
| `KEYCLOAK_CLIENT_ID` | Provisioning service client ID. Default: `sandbox-provisioner`. |
| `KEYCLOAK_CLIENT_SECRET` | **Required for the real adapter.** Default: empty. |
| `KEYCLOAK_SANDBOX_ROLE_NAMES` | Comma-separated existing realm role names. Default: `healthId,hip,hiu,hfr`. Confirm the approved role set for the environment. |

The Keycloak adapter authenticates with the `client_credentials` grant. It has no username/password authentication mode.

For the **WSO2 adapter**, configure:

| Variable | Requirement / default |
| --- | --- |
| `WSO2_BASE_URL` | **Set the actual service URL.** Default is the unusable placeholder `https://wso2.invalid`. |
| `WSO2_CLIENT_ID` | **Required for real OAuth authentication.** Default: empty. |
| `WSO2_CLIENT_SECRET` | **Required for real OAuth authentication.** Default: empty. |
| `WSO2_USERNAME` | **Required for the default password grant.** Default: empty. |
| `WSO2_PASSWORD` | **Required for the default password grant.** Default: empty. |
| `WSO2_SANDBOX_API_NAMES` | **Required for provisioning.** Approved comma-separated published API names, not IDs. Default: empty list; the provisioning chain rejects an empty configuration. |
| `WSO2_DEVPORTAL_PATH` | Default: `/api/am/devportal/v3`. |
| `WSO2_TOKEN_PATH` | Default: `/oauth2/token`. |
| `WSO2_GRANT_TYPE` | Default: `password`. |
| `WSO2_SCOPES` | Default: `apim:subscribe,apim:app_manage,apim:sub_manage`. |
| `WSO2_THROTTLING_POLICY` | Default: `Unlimited`. |
| `WSO2_TOKEN_TYPE` | Default: `JWT`. |
| `WSO2_KEY_MANAGER` | Default: `Resident Key Manager`. Match the actual configured key manager. |
| `WSO2_KEY_TYPE` | Default: `PRODUCTION`. |
| `WSO2_READ_TIMEOUT_SECONDS` | Default: `15` seconds. |

The WSO2 token request uses HTTP Basic client credentials and sends the grant type, username, password, and scopes in the form body. Changing `WSO2_GRANT_TYPE` does not remove the username/password fields from that request; an alternate grant needs to be supported by the target server.

For the **HIE-CM adapter and integrator-facing gateway URL**, configure:

| Variable | Requirement / default |
| --- | --- |
| `HIECM_BASE_URL` | **Set the actual internal service URL.** Default: `https://hiecm.invalid`. |
| `HIECM_CLIENT_ID` | **Required for the real adapter.** Default: empty. |
| `HIECM_CLIENT_SECRET` | **Required for the real adapter.** Default: empty. |
| `HIECM_BRIDGE_CALLBACK_BASE_URL` | **Set the actual callback base URL.** Default: `https://bridge.invalid`. The product reference is appended automatically. |
| `HIECM_API_PATH` | Default: `/api/v3`. |
| `HIECM_SESSION_PATH` | Default: `/sessions`. |
| `HIECM_CM_ID` | Default: `sbx`. |
| `ABDM_GATEWAY_URL` | Gateway URL exposed with credentials. Default: `https://dev.abdm.gov.in/gateway`. |

The session endpoint combines `HIECM_BASE_URL`, `HIECM_API_PATH`, and `HIECM_SESSION_PATH`. The adapter sends the configured client ID and secret, with `X-CM-ID` derived from `HIECM_CM_ID`.

Source for provisioning settings and behavior: `config/settings/base.py`, `ohc_experience/integrations/registry.py`, `ohc_experience/integrations/keycloak/adapter.py`, `ohc_experience/integrations/wso2/adapter.py`, `ohc_experience/integrations/wso2/apis.py`, `ohc_experience/integrations/hiecm/adapter.py`, `ohc_experience/integrations/tasks.py`, `ohc_experience/abdm/gateway.py`.

These **optional controls** tune application behavior, security, monitoring, builds, and background jobs:

| Variable | Default / purpose |
| --- | --- |
| `DJANGO_DEBUG` | `False`. Keep disabled for production. |
| `DJANGO_READ_DOT_ENV_FILE` | `False`. Enable only when intentionally loading an available root `.env` file. Must be supplied externally to enable that file's loading. |
| `DJANGO_ACCOUNT_ALLOW_REGISTRATION` | `True`. |
| `DJANGO_ADMIN_FORCE_ALLAUTH` | `False`. |
| `DJANGO_SECURE_SSL_REDIRECT` | `True`. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True`. |
| `DJANGO_SECURE_HSTS_PRELOAD` | `True`. |
| `DJANGO_SECURE_CONTENT_TYPE_NOSNIFF` | `True`. |
| `CONN_MAX_AGE` | `60` seconds. Database connection reuse. |
| `COMPRESS_ENABLED` | `True`. Controls runtime use of compressed assets. The image's `/build-static` script explicitly enables compression when generating offline assets; `/start` does not build or compress assets. Keep the production default to use the baked compressor manifest. |
| `DJANGO_SENTRY_LOG_LEVEL` | `20` (INFO). |
| `SENTRY_ENVIRONMENT` | `production`. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0`. |
| `PROVISIONING_MAX_ATTEMPTS` | `5`. |
| `PROVISIONING_RETRY_BACKOFF_SECONDS` | `120` seconds. |
| `PROVISIONING_RETRY_BACKOFF_MAX_SECONDS` | `900` seconds. |
| `SECRET_REF_TTL_SECONDS` | `900` seconds. Lifetime of temporary credential references in the shared cache. |
| `TAILWIND_USE_STANDALONE_BINARY` | `True`. Relevant when building through Django's Tailwind command; the Docker frontend stage uses npm. |
| `NPM_BIN_PATH` | `/usr/bin/node`. Optional Django Tailwind tooling override. |

**Flower**, included in the production Compose stack, additionally requires:

| Variable | Requirement |
| --- | --- |
| `CELERY_FLOWER_USER` | **Required when running Flower.** Basic-auth username. |
| `CELERY_FLOWER_PASSWORD` | **Required when running Flower.** Basic-auth password. |

Flower also directly requires `REDIS_URL`, listed with the core settings above.

Source: `config/settings/base.py`, `config/settings/production.py`, `config/settings/static_assets.py`, `compose/production/django/build-static`, `compose/production/django/start`, `compose/production/django/celery/flower/start`.

These **legacy names** remain in configuration for compatibility:

| Variable | Status |
| --- | --- |
| `SANDBOX_CREDENTIAL_KEY` | Fallback for `EXPERIENCE_CREDENTIAL_KEY`. Preserve the same encryption key when renaming. |
| `SANDBOX_GATEWAY_URL` | Fallback for `ABDM_GATEWAY_URL`. |
| `ABDM_CREDENTIAL_PROVIDER` | Legacy setting with no current Python runtime consumer. Not required by the current provisioning chain. |
| `SANDBOX_CREDENTIAL_PROVIDER` | Fallback for the unused `ABDM_CREDENTIAL_PROVIDER` setting. |
| `LGD_PROVIDER` | Unused setting. The default `ohc_experience.organisations.lgd.api_lookup` and local override `ohc_experience.organisations.lgd_local.lookup` do not resolve to current implementations. Changing this variable does not change address lookup. Configure `LGD_API_URL` and `LGD_API_KEY` instead. |

The current provisioning chain uses the three `INTEGRATION_*` adapters. Older documentation claiming that `ABDM_CREDENTIAL_PROVIDER` must be supplied is stale.

These **local development and simulation variables** are included for completeness; they are not requirements for a deployment using the real adapters:

| Variable | Purpose |
| --- | --- |
| `LOCAL_KEYCLOAK_REALM_ROLES` | Local adapter's simulated role inventory. Default: `bridge,hip,hiu,healthId,health_locker,phr,hfr,hp_id,OIDC,HidAbhaSearch,DIGI_DOCTOR,HIP_PAYER,HIU_PAYER`. |
| `LOCAL_BRIDGE_ACTIVATION_DELAY_SECONDS` | Local adapter activation delay. Default: `5` seconds. |
| `USE_DOCKER` | Local settings switch. Default: `no`. |
| `DJANGO_USE_LOCAL_MEDIA` | Local filesystem upload switch. Default: true outside Docker, false in Docker. |
| `EMAIL_HOST` | Local SMTP hostname. Default: `mailtrap-local`. |
| `DJANGO_HOST_PORT` | Local Compose published web port. Default: `8010`. |
| `IPYTHONDIR` | Local interactive-shell configuration directory. |
| `MINIO_ROOT_USER` | Local MinIO container administrator username. |
| `MINIO_ROOT_PASSWORD` | Local MinIO container administrator password. |
| `KC_BOOTSTRAP_ADMIN_USERNAME` | Local Keycloak bootstrap administrator username. |
| `KC_BOOTSTRAP_ADMIN_PASSWORD` | Local Keycloak bootstrap administrator password. |
| `KC_HEALTH_ENABLED` | Local Keycloak container health endpoint setting. |

Source: `config/settings/local.py`, `config/settings/base.py`, `docker-compose.local.yml`.

For **deployment configuration placement and remaining setup**:

1. Production Compose loads `.envs/.production/.django` and `.envs/.production/.postgres` into Django, worker, beat, and Flower. PostgreSQL loads only `.postgres`; the AWS helper loads only `.django`. Put all five PostgreSQL variables in `.postgres` and application/integration variables in `.django`. These production env files were absent from the audited checkout.
2. The Docker build excludes `.env` and `.envs`. Production Compose does not automatically inject the root `.env` contents into application containers. Supply values through the production env files or the hosting platform's runtime environment.
3. Use the same production settings, database, shared Redis, and encryption key for web and background workers. Configure email settings on both the web process and worker. Run only one Celery beat scheduler.
4. The image build runs `/build-static` with `config.settings.build` to collect static files, generate offline compression assets, compile translations, and verify the resulting assets without a live database or production secrets. `/start` only starts Gunicorn using those baked assets; it does **not** build assets or run database migrations. Do not mount an empty volume over `/app/staticfiles` or `/app/theme`. Run `python manage.py migrate` with production settings as a separate deployment step.
5. The stock Gunicorn command binds to `0.0.0.0:5000`; it does not read a `PORT` variable. Configure the hosting service to route to port `5000`, or change the startup command.
6. Traefik's web/Flower hostnames and certificate contact email are hardcoded in `compose/production/traefik/traefik.yml`. Update them for the deployed domain; `DJANGO_ALLOWED_HOSTS` alone does not change routing. The app expects the proxy's `X-Forwarded-Proto` to identify HTTPS.
7. `CSRF_TRUSTED_ORIGINS` and SMTP connection settings are not mapped from environment variables in production settings. Adding those environment names alone does not configure Django. The default mail path is the Global Email API.
8. Environment values do not create the required database, Redis service, private bucket, approved email templates, Turnstile registration, Keycloak service client, WSO2 APIs/key manager, or HIE-CM access. Those resources and network routes must exist for their features to operate.
9. The GHCR publishing workflow uses the automatic `GITHUB_TOKEN`; no extra manually supplied build secret is configured. `APP_HOME` is an optional Docker build argument (default `/app`), while `UV_COMPILE_BYTECODE`, `UV_LINK_MODE`, `UV_PYTHON_DOWNLOADS`, and `PATH` are preset image/build settings, not required application runtime inputs.

Source: `docker-compose.production.yml`, `.dockerignore`, `compose/production/django/Dockerfile`, `compose/production/django/build-static`, `config/settings/build.py`, `compose/production/django/start`, `compose/production/traefik/traefik.yml`, `.github/workflows/publish-image.yml`.

Scope: repository-defined runtime variables and deployment-script inputs at the revision above. This inventory does not claim to enumerate every environment variable recognized internally by PostgreSQL, Redis, Gunicorn, AWS CLI, Python, or the hosting platform. No live service connectivity or credential validity was tested when preparing it.
