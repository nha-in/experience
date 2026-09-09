# ABDM Developer Sandbox

The September 2026 v3 design is implemented as a Django/HTMX portal on top of the
existing product, application, reusable form, attachment and outcome engine.
Historical migrations are retained for fresh installs and existing databases.
Cleanup migrations drop retired Care provisioning and generic application
access/query/audit tables and unused fields; current portal records are retained.

The reusable engine, models, permissions, views and tasks belong to the
`experiences` Django app. ABDM is a plain Python implementation package with
forms, catalog, hooks and gateway policy, not an installed app. See the
[engine boundary and extension contracts](../ohc_experience/experiences/README.md)
and [ABDM package guide](../ohc_experience/abdm/README.md). There is no `sandbox`
app; migrations adopt its existing tables and preserve admin permission grants.

## Local Demo

```sh
docker compose -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.local.yml exec django python manage.py migrate
docker compose -f docker-compose.local.yml exec django python manage.py seed_experience_demo --reset
```

The reset command is deliberately destructive: it deletes all application data,
accounts and referenced uploaded documents before creating the demo. It is
restricted to DEBUG environments. Do not run it against a database to retain.
Local uploads use private MinIO. File type and size are validated before storage;
uploads do not undergo antivirus scanning.

Open http://localhost:8000/portal/ or http://localhost:8000/assess/dashboard/.
Demo accounts all use password `experience-demo-2026`:

| Email | Access |
| --- | --- |
| applicant@abdm-demo.in | Primary organisation owner |
| contributor@abdm-demo.in | Integrator teammate |
| reviewer@abdm-demo.in | Assigned NHA reviewer |
| admin@abdm-demo.in | NHA administrator; assigns reviewers |
| new-integrator@abdm-demo.in | Organisation awaiting verification |

`SBX-2026-00001` demonstrates an approved shared M1, an M2 query, locked M3/M4,
a PHR1 review, a sent-back HealthLocker request and a UHI draft. The second
product awaits registration. Events, PDF evidence, a support conversation and
pending organisation verification are included. IDs use the year at seed time.
Local mail is visible at http://localhost:3550/.

## Running from a shell

With PostgreSQL running locally:

```sh
uv sync --frozen
npm ci --prefix theme/static_src
npm run build --prefix theme/static_src
export DATABASE_URL=postgres:///ohc_experience_port
export REDIS_URL=redis://localhost:6379/0
export USE_DOCKER=no
export DJANGO_SETTINGS_MODULE=config.settings.local
createdb ohc_experience_port
uv run python manage.py migrate
uv run python manage.py seed_experience_demo
uv run python manage.py runserver
```

Choose a fresh database name for the demo; omit `createdb` and seeding when
upgrading an existing database. Without Docker, uploads use local disk and mail
uses the console. Set `DJANGO_USE_LOCAL_MEDIA=false` to use configured object
storage from the shell. Docker keeps its MinIO and mail service defaults.

Request handlers enqueue mail in `Notification` records. Run a Celery worker and
beat for automatic delivery, or process queued mail once with:

```sh
uv run python manage.py shell -c 'from ohc_experience.experiences.tasks import deliver_notifications; deliver_notifications()'
```

The UI from `bodhi-test` is implemented against the reusable engine's existing
models, migrations, services and routes. Product registration defaults to HMIS,
Clinical HMIS and HI-CM M1 for a new product; editing preserves saved choices.
Support tickets default to Medium priority and Sandbox category, and their
optional track is validated against the selected product's applied tracks.
Reviewer dashboards, queues, decisions and submission history retain the engine's
assignment and permission rules. Retired Care provisioning and OHC routes remain
retired; reviewer work uses the engine's assessment screens.

## Model Mapping

- `ProductWorkspace` extends the existing `Product` with its reference and program key,
  registration state, solution type and selected track/milestone pairs.
- Each canonical `Milestone` has an `ApplicationInstance`. HI-CM M1 and PHR M1
  share the same milestone and approval. HealthLocker does not require M3.
- `ReviewItem` wraps organisation verification, product registration or exit.
  Admins assign reviewers manually. Only the assignee or a superuser may decide.
- `FormRecord` remains independent. Exit requests for one product share a form
  record. `ApplicationFormUse` and `ReviewItem` pin the selected submission.
- `FormSubmission` preserves JSON answers, field schema/version, submission
  occurrence, revision and attached files. Reusing evidence never rewrites the
  other application's pin. Editing creates a new revision; saving evidence under
  another application starts a fresh occurrence. Historical screens render the
  stored schema, so later form-definition changes do not hide old fields.
- Product-level `ProductOutcome` records contain credential references and
  milestone decisions, including production handoff notes. Secrets never appear
  in outcome JSON, notification emails or reviewer pages.
- `ReviewQuery` is pinned to the reviewed submission. All open queries must be
  answered, and answered queries resolved, before approval. Forms under review
  are read-only until withdrawn or sent back.
- `AuditEvent` is append-only through the application and ORM. Review forms,
  decisions, queries, assignment and credential actions create audit entries.
  This is not protection against a privileged database administrator; use database
  audit/retention controls when deploying in a regulated environment.

The fixed catalog lives in `abdm/catalog.py`. M4 is HFR Registration, PHR
shares M1 with HI-CM, and NHCX intentionally has no published milestones per v3.
There is no configured decision SLA. Production credential issuance is external;
approval notes are emailed to integrators and retained as product outcomes.

## Routes and Frontend

- `/accounts/signup/`: account creation, CAPTCHA and email verification.
- `/onboarding/organisation/`, `/onboarding/product/`: initial registration.
- `/portal/products/`: product list and switcher.
- `/products/<sandbox-id>/`: overview and activity.
- `/products/<sandbox-id>/tracks/<track>/`: milestone forms, queries and history.
- `/products/<sandbox-id>/credentials/`: audited reveal, rotation and callbacks.
- `/portal/queries/`: highlighted pending queries.
- `/assess/dashboard/`, `/assess/queue/`, `/assess/review/<id>/`: NHA review.
- `/portal/events/`, `/portal/support/`: registrations and support threads.

The CareUI shell uses HTMX navigation with a shared `#main-content` target and
an out-of-band `#app-nav` refresh, with local HTMX history caching disabled.
Forms preserve ordinary POST/redirect behavior. The product switcher in the
sidebar and breadcrumb follows the selected product into team and profile pages;
the sidebar lists only its applied tracks, with approved/applied counts. Upload controls retain saved files, append sequential
selections, permit removal and preserve previous revision attachments. Downloads
are permission-checked and streamed from private object storage; object keys and
public media URLs are not exposed. Desktop and mobile share the same templates.
Dates are shown in Asia/Kolkata. Account settings, team management and Django
admin remain available. Team and profile pages share the same shell. Transitions to sign-in and
administration use full-page navigation. Retired OHC, generic application, Care provisioning, demo
and user API routes are no longer exposed.

## Production Configuration

The production Docker image builds Tailwind CSS from the checked-in templates
and lockfile, then copies the stylesheet into the Python image. No local Node
dependencies or prebuilt stylesheet are required; startup collects the generated
CSS alongside the bundled fonts and other static files.

Use the production Compose stack with its Django, Celery worker, Celery beat,
PostgreSQL and Redis services. Supply normal Django HTTPS, host, database
and email settings plus the following secrets in deployment configuration:

| Setting | Requirement |
| --- | --- |
| `EXPERIENCE_CREDENTIAL_KEY` | Dedicated Fernet key, kept in a secret manager, separate from Django's secret key. Back it up with appropriate access controls. |
| `ABDM_CREDENTIAL_PROVIDER` | Dotted Python callable implementing real gateway provisioning. Required before issuing any real credentials. |
| `ABDM_GATEWAY_URL` | Approved sandbox gateway endpoint, if using the local demo provider. |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Registered Cloudflare Turnstile site keys for the deployed hostname. |
| `DJANGO_AWS_ACCESS_KEY_ID`, `DJANGO_AWS_SECRET_ACCESS_KEY` | Credentials scoped to the upload bucket. |
| `DJANGO_AWS_STORAGE_BUCKET_NAME`, `DJANGO_AWS_S3_REGION_NAME` | Private S3 bucket and region. |
| `DJANGO_AWS_S3_ENDPOINT_URL` | Optional S3-compatible endpoint. Local settings use MinIO. |

The previous `SANDBOX_CREDENTIAL_KEY`, `SANDBOX_CREDENTIAL_PROVIDER` and
`SANDBOX_GATEWAY_URL` environment names remain fallbacks during deployment
upgrades. Keep the same encryption key value when renaming its environment
variable. The engine stores encrypted credentials in `ProductCredential`.

Turnstile tokens are verified server-side, including their hostname, using
[Cloudflare's Siteverify API](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).
The arithmetic challenge is a local development fallback only. Production signup
fails closed when Turnstile is not configured. Email verification remains enabled.

The credential provider is called as `provider(product=product, operation=operation)`.
For `issue` and `rotate`, return `client_id`, `client_secret` and `gateway_url`.
For `revoke`, revoke the product's active credentials at the gateway; the return
value is ignored. The provider should use timeouts, be idempotent for the product
and operation, and must not log secrets. It runs within the workflow transaction;
remote gateway writes cannot be rolled back with PostgreSQL, so reconciliation
and retry behavior must be implemented by the integration. No real NHA provisioning
API is bundled. Development credentials are visibly marked as nonfunctional demo
values, and demo issuance is disabled in production.

Client secrets are encrypted at rest with the dedicated key. Reveal is integrator
only, POST/CSRF protected, audited, rate-limited and returned with no-store headers.
The browser masks a revealed value after 30 seconds or when the tab is hidden.
Organisation re-verification revokes prior active credentials; after verification,
the integrator can request rotation to obtain a fresh pair.

Keep S3 public access blocked and enable appropriate encryption and retention.
The application accepts PDF evidence up to 10 MB per file and validated logo
images. Historical files remain available with their original revision. File type
and size validation do not detect malware; uploaded documents should be treated
as untrusted content.

Celery beat delivers queued emails every minute, checks public HTTPS callbacks
every 15 minutes, and checks event reminders hourly. Three consecutive callback
failures trigger a notification. The callback checker blocks private/reserved IPs,
validates HTTPS certificates and does not follow redirects. Run only one beat
scheduler; inspect unsent `Notification` rows for delivery failures (five attempts).

## Verification

```sh
docker compose -f docker-compose.local.yml exec django python manage.py check
docker compose -f docker-compose.local.yml exec django python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.local.yml exec django pytest -q
```

The sandbox regression suite covers shared milestone dependencies, assignment,
organisation scoping, read-only reviews, query state transitions, withdrawal,
resubmission history, schema snapshots, multi-file append/removal, CSRF,
credential encryption and reveal limits, callback address validation, queue
filters, legacy-route protection, and file type/size validation. Signup tests cover
local challenge expiry and remote CAPTCHA verification failures.

Engine tests additionally run an unrelated supplier-quality implementation and
verify that Django starts without importing ABDM. The upgrade preserves current
data; only invoke the demo reset command when deliberately starting afresh.
