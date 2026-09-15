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

Configure `LGD_API_KEY` in the Git-ignored root `.env` before starting the container
and seeding organisations. Local Docker services read this bind-mounted file;
it is excluded from image builds. Keep secrets out of the tracked
`.envs/.local/.django`. Native shell runs can set `DJANGO_READ_DOT_ENV_FILE=true`
to load the same file, or supply `LGD_API_KEY` in their environment.
The demo's PIN code is validated against LGD, so seeding needs working API access;
only automated tests substitute controlled lookup fixtures. There is no built-in
PIN mapping or fake fallback. See [Organisation address lookup](#organisation-address-lookup)
for the service settings. Seeding checks the demo PIN before changing data,
including before a `--reset`; an unavailable or empty lookup aborts that check.
The `--permissions-only` mode does not need LGD access.

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
| nhcx-reviewer@abdm-demo.in | Reviews: NHCX only |
| uhi-reviewer@abdm-demo.in | Reviews: UHI only |
| hiecm-reviewer@abdm-demo.in | Reviews: HIE-CM only |
| nhcx-support@abdm-demo.in | Support: NHCX only |
| uhi-events@abdm-demo.in | Events: UHI only |

Staff grants are independent per area (Reviews, Support, Events) and category,
with separate read, write and approve checkboxes. Superadmins manage staff and
permissions at `/portal/staff/` using the Staff & permissions navigation entry.
They can create/edit staff accounts, set passwords, search/filter the directory,
archive/restore staff, and edit permissions without using Django admin.
New staff accounts are portal-only and have no access by default;
ordinary Django permissions or reviewer assignment cannot bypass the grants.
The broad `reviewer@abdm-demo.in` demo account has explicit all-category grants;
the category-specific accounts above do not. All use the demo password above.
Applicant access stays organisation-based.

Review write allows queries; approve allows approval/send-back. Neither needs
assignment: the assignee only labels work for the queue filters. Support write
allows replies; approve allows resolution. Event
write allows draft creation/editing at `/portal/events/manage/`; approve allows
publishing through that page's actions. Published events must be unpublished before editing.
General/onboarding is a separate category covering organisation/product review;
it is not implicitly granted with NHCX or UHI. See the engine guide for details.
NHCX currently has no published milestone forms, so its review queue is empty
until the catalog defines those forms; permission grants are already supported.

Archiving disables login, ends existing sessions and releases pending review and
support assignments, without deleting the user's history or grants. Restoring
reactivates saved permissions but requires a new sign-in. Superadmin accounts
are protected from edit/archive in this interface. Account changes are audited;
passwords are validated and never logged. No database reset or new schema
migration is needed for the staff portal. The technical Django admin endpoint
remains separate for maintenance and is no longer linked from the portal.

To add the permission demo accounts to an existing local demo without clearing
application data:

```sh
docker compose -f docker-compose.local.yml exec django python manage.py seed_experience_demo --permissions-only
```

`SBX-2026-00001` demonstrates an approved shared M1 with a recorded (fake)
production client ID, an M2 query, an M3 review waiting on M2, a PHR1 review,
a sent-back HealthLocker request and a recorded UHI application. The second
product is registered, with no milestone requests yet. Events, PDF evidence, a support conversation and
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

Workflow handlers enqueue mail in `Notification` records. The production
[Global Email integration](global_email.md) also queues account emails and
organisation invitations, using Anymail and the documented ABDM/NIC gateway.
Run a Celery worker and beat for automatic delivery, or process queued mail once
with:

```sh
uv run python manage.py shell -c 'from ohc_experience.experiences.tasks import deliver_notifications; deliver_notifications()'
```

The UI from `bodhi-test` is implemented against the reusable engine's existing
models, migrations, services and routes. Product registration defaults to HMIS,
Clinical HMIS and HIE-CM M1 for a new product; editing preserves saved choices.
Support tickets default to Medium priority and Sandbox category, and their
optional track is validated against the selected product's applied tracks.
Reviewer dashboards, queues, decisions and submission history retain the engine's
assignment and permission rules. Retired Care provisioning and OHC routes remain
retired; reviewer work uses the engine's assessment screens.

## Model Mapping

- `ProductWorkspace` extends the existing `Product` with its reference and program key,
  registration date, solution type and selected track/milestone pairs.
- Each canonical `Milestone` has an `ApplicationInstance`. HIE-CM M1 and PHR M1
  share the same milestone and approval. HealthLocker does not require M3.
- `ReviewItem` wraps organisation verification, product registration or exit.
  Product registration is recorded rather than reviewed: registering or editing a
  product applies at once, keeps its revision history and never enters the
  review queue. Registration starts sandbox provisioning, whether or not the
  organisation is verified yet.
- Milestones are never locked. An integrator can submit M2 before M1 is approved,
  or before the organisation is verified. The review keeps the order instead:
  approve and send back stay disabled, and are refused, while an earlier
  milestone or organisation verification is unapproved. Queries can still be
  raised. UHI participation submitted early waits, and is recorded automatically
  once its prerequisites are approved.
  Admins assign reviewers manually to label and filter work; the assignee must
  hold the matching category's review-write or review-approve grant. Any
  reviewer with that grant can act, assigned or not; superusers can perform
  every action.
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
- `Product.production_client_id` holds the production client ID staff record once
  an exit is approved, unique across products whatever its case. The NHA gateway
  team issues production credentials and sends the secret to the integrator
  directly; the portal never holds it.
- `ReviewQuery` is pinned to the reviewed submission. All open queries must be
  answered, and answered queries resolved, before approval. Forms under review
  are read-only until withdrawn or sent back.
- `AuditEvent` is append-only through the application and ORM. Review forms,
  decisions, queries, assignment and credential actions create audit entries.
  This is not protection against a privileged database administrator; use database
  audit/retention controls when deploying in a regulated environment.

The fixed catalog lives in `abdm/catalog.py`. M4 is HFR Registration, PHR
shares M1 with HIE-CM, and NHCX intentionally has no published milestones per v3.
There is no configured decision SLA. Production credential issuance is external:
once a milestone exit is approved, staff with General/onboarding review approve
access record the production client ID the gateway team issued, at
`/assess/production/`. Recording or changing it is audited and emails the
organisation's members a link to the product (not the ID itself); removal is
audited only. Approval notes are emailed to integrators and retained as product
outcomes.

## Routes and Frontend

- `/accounts/signup/`: account creation, CAPTCHA and email verification.
- `/onboarding/organisation/`, `/onboarding/product/`: initial registration.
- `/portal/products/`: product list and switcher.
- `/portal/products/<sandbox-id>/`: NHA staff view of a product. Its open
  requests, milestones, registration and WASA each link to their review.
- `/products/<sandbox-id>/`: integrator overview and activity. Staff are
  redirected to the staff product view.
- `/products/<sandbox-id>/tracks/<track>/`: milestone forms, queries and history.
  Staff are redirected to the selected milestone's review.
- `/products/<sandbox-id>/credentials/`: audited reveal, rotation and callbacks.
- `/portal/queries/`: highlighted pending queries.
- `/assess/dashboard/`, `/assess/queue/`, `/assess/review/<id>/`: NHA review.
- `/assess/production/`, `/assess/production/<sandbox-id>/`: products with an
  approved exit, their production client IDs, and a CSV export at
  `/assess/production/export/`. General/onboarding review read to view, approve
  to record, change or remove.
- `/portal/events/`, `/portal/support/`: registrations and support threads.

The CareUI shell uses HTMX navigation with a shared `#main-content` target and
an out-of-band `#app-nav` refresh, with local HTMX history caching disabled.
Forms preserve ordinary POST/redirect behavior. The product switcher in the
sidebar and breadcrumb follows the selected product into team and profile pages;
the sidebar lists only its applied tracks, with approved/applied counts. Staff
pages never show the switcher; staff reach products from lists and reviews.
Upload controls retain saved files, append sequential
selections, permit removal and preserve previous revision attachments. Downloads
are permission-checked and streamed from private object storage; object keys and
public media URLs are not exposed. Desktop and mobile share the same templates.
Dates are shown in Asia/Kolkata. Account settings, team management and Django
admin remain available. Team and profile pages share the same shell. Transitions to sign-in and
administration use full-page navigation. Retired OHC, generic application, Care provisioning, demo
and user API routes are no longer exposed.

## Visual design

The portal adapts Amjith Titus’s Layered styling from `bodhi-test` commit
`4bd4f07`: emerald hero bands, floating summary cards, connected milestone
stations, evidence readiness, review history, and support/event count tabs.
The shared components live in `theme/static_src/src/layered.css`; hero bands
remain inside the HTMX main-content target and queue filters refresh their
hero counts out of band.

These templates use the engine’s existing models, forms and permission checks.
Readiness counts saved required fields from each active form schema, shared
milestones are counted once, and query indicators distinguish unanswered
questions from replies awaiting review. The evidence schema remains unchanged.

Forms show linked error summaries and protect unfinished edits when leaving.
Mobile evidence forms put saved progress before the fields, and incomplete
submissions link to the first field needing attention. Shared request feedback
keeps entries visible after HTMX failures and restores keyboard focus after
navigation. Reviewer filters keep status choices consistent with the selected
scope; the reviewer query inbox also includes received replies awaiting resolution.

## Production Configuration

The production Docker image builds Tailwind CSS from the checked-in templates
and npm lockfile, then runs `collectstatic`, offline compression, and an asset
smoke check during the image build. The final image includes the generated CSS,
bundled fonts, hashed static manifest, and compressor manifest in
`/app/staticfiles`. No local Node dependencies, prebuilt stylesheet, runtime
asset compilation, database, or production secrets are needed to build assets.
Host `staticfiles/` and generated CSS are excluded from the Docker context.

Build from the repository root:

```sh
docker build -f compose/production/django/Dockerfile -t experience-production .
```

The existing [Publish sandbox image workflow](../.github/workflows/publish-image.yml)
builds this same Dockerfile for ARM64 and publishes it to the Amazon ECR
repository configured by the repository's `AWS_REGION` and `ECR_REPOSITORY`
Actions variables. It includes the asset build and verification, so no
additional production image build job is required.

For AWS ECS, deploy a new task revision using the published image's new tag or
digest; existing tasks do not pick up changed images automatically
([ECS task image configuration](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html#container_definition_image)).
The published image is `linux/arm64` only, so the task's runtime platform must be
`ARM64`. Use `DJANGO_SETTINGS_MODULE=config.settings.production` (the
image default), container port `5000`, and the default `/start` command. The
existing `/entrypoint` expects `POSTGRES_HOST`, `POSTGRES_PORT`, and
`POSTGRES_USER` alongside your normal database configuration. ECS command
overrides that start Gunicorn directly also have the same baked assets. Do not
mount an empty volume over `/app/staticfiles` or `/app/theme`, and do not run
`collectstatic` in a separate task expecting to populate another task's filesystem.

Set the ALB health-check path to `/ping/` on container port `5000`, expecting
HTTP `200`. This public liveness endpoint returns plain `OK` without checking
the database or other services. Only this exact path bypasses HTTPS redirects,
host validation, and session/authentication middleware so private-IP probes work.

The production Beat command `/start-celerybeat` runs
`python /app/manage.py migrate --noinput` before starting the scheduler. Migration
failure stops startup. Run one Beat task, prevent overlapping Beat replacements,
and deploy its new image on every release that includes migrations. This gates
Beat only, not the web or worker services: use backward-compatible migrations or
sequence their deployment after migrations complete. The Beat database user needs
permission to apply schema changes. Local Beat startup is unchanged.

WhiteNoise serves `/static/` through Gunicorn, so route that path to the same
ECS service through the load balancer. S3 is used for private uploads, not these
public assets. See the [WhiteNoise deployment guide](https://whitenoise.readthedocs.io/en/stable/django.html).
The `config.settings.build` settings are strictly for offline asset creation
and verification, never for serving the application. The Dockerfile's asset
check runs during each publishing build without a database or runtime secrets.

Use the production Compose stack with its Django, Celery worker, Celery beat,
PostgreSQL and Redis services. Supply normal Django HTTPS, host, database
and email settings plus the following secrets in deployment configuration:

| Setting | Requirement |
| --- | --- |
| `EXPERIENCE_CREDENTIAL_KEY` | Dedicated Fernet key, kept in a secret manager, separate from Django's secret key. Back it up with appropriate access controls. |
| `ABDM_CREDENTIAL_PROVIDER` | Dotted Python callable implementing real gateway provisioning. Required before issuing any real credentials. |
| `ABDM_GATEWAY_URL` | Approved sandbox gateway endpoint, if using the local demo provider. |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Registered Cloudflare Turnstile site keys for the deployed hostname. |
| `DJANGO_AWS_ACCESS_KEY_ID`, `DJANGO_AWS_SECRET_ACCESS_KEY` | Optional explicit credentials. Omit both when using an ECS task role. |
| `DJANGO_AWS_STORAGE_BUCKET_NAME`, `DJANGO_AWS_S3_REGION_NAME` | Private S3 bucket and region. |
| `DJANGO_AWS_S3_ENDPOINT_URL` | Optional S3-compatible endpoint. Local settings use MinIO. |

For ECS role-based S3 access, attach the upload permissions to the task's
`taskRoleArn`, not only its `executionRoleArn`. Leave both `DJANGO_AWS_*` key
variables unset and remove any stale `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
or `AWS_SESSION_TOKEN` overrides so the SDK can obtain and refresh task-role
credentials. Keep the bucket name and region configured; no S3 endpoint override
is needed for AWS S3. The bucket remains private and download URLs stay signed.
See [ECS task IAM roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
and [django-storages authentication](https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html#authentication-settings).

The previous `SANDBOX_CREDENTIAL_KEY`, `SANDBOX_CREDENTIAL_PROVIDER` and
`SANDBOX_GATEWAY_URL` environment names remain fallbacks during deployment
upgrades. Keep the same encryption key value when renaming its environment
variable. The engine stores encrypted credentials in `ProductCredential`.

### Redis authentication

For authenticated ElastiCache, configure every web, Celery worker, Celery beat,
and Flower container with:

```text
REDIS_URL=rediss://your-redis-endpoint:6379/0
REDIS_AUTH_TOKEN=<raw Redis AUTH token injected from your secret manager>
```

`REDIS_AUTH_TOKEN` supplies the password to the Django cache, Celery broker and
result backend, including Flower. Supply the raw token, not a URL-encoded value.
Leave credentials out of `REDIS_URL`; if it already contains a password, that
password takes precedence. An ACL username can be included as
`rediss://username@your-redis-endpoint:6379/0`. Unset or empty tokens preserve the
existing unauthenticated local configuration (`redis://redis:6379/0`). This does
not enable authentication on the bundled Redis server itself.

Use `rediss://` for TLS: ElastiCache AUTH requires encryption in transit.
TLS connections verify the server certificate and hostname. See
[ElastiCache AUTH](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/auth.html).

Inject `REDIS_AUTH_TOKEN` using the ECS container definition's `secrets` entries,
with `name` set to `REDIS_AUTH_TOKEN` and `valueFrom` set to your Secrets Manager
secret ARN. Keep `REDIS_URL` in ordinary environment variables. Redeploy all
consumers after changing or rotating the secret; existing tasks do not refresh
injected environment variables. See
[ECS secret injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html).

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
scheduler; inspect unsent `Notification` rows for delivery failures (up to five
attempts with backoff; uncertain Global Email outcomes require review).

## Organisation address lookup

Organisation registration and editing use the six-digit Indian PIN code to look
up State and District through the ABDM Local Government Directory (LGD) service.
One distinct state/district match fills both fields automatically. Multiple
locality records for the same LGD code pair count as one match; when a PIN maps
to multiple state/district pairs, the applicant selects the applicable result.
Changing the PIN clears its previous location. An unknown PIN needs correction,
while a service failure retains the entered PIN and offers a retry.

The server validates the PIN and selected location on every form submission,
using the authoritative LGD result or its bounded cache. Browser autofill and
submitted names or codes cannot bypass this validation. Lookup failures prevent
saving an unverified location. Canonical State and District names and their LGD
codes are stored in the form's existing JSON answers. This requires no database
migration; historical submissions retain their recorded values and schema.
The metadata keys are `state_lgd_code` and `district_lgd_code`.

| Environment setting | Default and purpose |
| --- | --- |
| `LGD_API_KEY` | Required server-side API key. Supply through the deployment's secret configuration; never include it in templates, JavaScript or committed files. |
| `LGD_API_URL` | `https://apissbx.abdm.gov.in/global/api/v3/internal/lgd`. Override with the approved HTTPS LGD base for the deployment. |
| `LGD_API_TIMEOUT` | `5.0` seconds per provider connection/read operation; must be greater than zero and at most 30. |
| `LGD_CACHE_TTL` | `3600` seconds; allowed range 0–86400. Set 0 to disable caching. Each worker caches at most 512 PIN results; provider failures are not cached. |

The server calls `GET {LGD_API_URL}/search?pinCode={PIN}&view=All` with the
`apikey`, `REQUEST-ID` and `TIMESTAMP` headers. Timestamps use UTC with exactly
three fractional digits and a trailing `Z`. The provider returns an array with
`stateName`, `stateCode`, `districtName`, `districtCode` and locality fields;
the application keeps the distinct state/district code pairs. Credentials and
provider error bodies are not returned to the browser.

A live sandbox check on 9 September 2026 returned HTTP 200 for PIN `560001`,
with `KARNATAKA` / state code `29` and `BENGALURU URBAN` / district code `525`.
The millisecond UTC timestamp format was verified against that service. This
check establishes the sandbox contract used here; deployment credentials remain
environment-specific.

The source flow was traced in the supplied `ABDM.zip` archive under
`FE_source_code_abdm-sandbox/sandbox-website/src/`:

- `store/actions/common-action.js`: `getStateDistrictVillage` calls `/search`.
- `hooks/use-axios.js`: supplies the LGD API key, request ID and timestamp.
- `pages/sandbox-updated-registration/components/organization-registration-form.js`:
  fills State and District from the first result after six PIN digits.

The current implementation preserves the lookup behavior and adds explicit
selection for ambiguous results, retry feedback and server validation.

## WASA certification agencies

The agency dropdown uses the `CertificationAgency` database table. Apply
migrations with `python manage.py migrate`; migrations 0007–0008 create the table
and import all 255 options from the old portal as initial ABDM entries.

Superusers manage the list in Django admin under **Experiences → Certification
agencies**, normally at `/admin/experiences/certificationagency/`. They can add or
rename entries, set display order and deactivate/reactivate them. The dropdown
reads active ABDM entries on each request, so edits require no code deployment.
Deletion through admin is disabled; deactivate agencies that should no longer
be offered for new selections.

Submitted forms keep their recorded agency names. A saved name remains available
on that form after an agency is renamed or deactivated. Demo resets preserve
the administrator-maintained table; demo creation requires at least one active
ABDM agency. The legacy list is seed data, with no automatic external refresh.

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
Organisation lookup tests use controlled provider responses to cover autofill,
ambiguous matches, invalid and unknown PINs, service failures, and server-side
location validation without requiring live credentials.

Engine tests additionally run an unrelated supplier-quality implementation and
verify that Django starts without importing ABDM. The upgrade preserves current
data; only invoke the demo reset command when deliberately starting afresh.
