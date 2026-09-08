# ABDM Developer Sandbox

The September 2026 v3 design is implemented as a Django/HTMX portal on top of the
existing product, application, reusable form, attachment and outcome engine.
No legacy records are transformed. The added migrations create schema only.

## Local Demo

```sh
docker compose -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.local.yml exec django python manage.py migrate
docker compose -f docker-compose.local.yml exec django python manage.py seed_sandbox_demo --reset
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

## Model Mapping

- `ProductWorkspace` extends the existing `Product` with its sandbox ID,
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

The fixed catalog lives in `sandbox/catalog.py`. M4 is HFR Registration, PHR
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

The shell uses HTMX boosted links/forms with a shared `#portal` target and no
local HTMX history cache. Upload controls retain saved files, append sequential
selections, permit removal and preserve previous revision attachments. Downloads
are permission-checked and streamed from private object storage; object keys and
public media URLs are not exposed. Desktop and mobile share the same templates.
Dates are shown in Asia/Kolkata. Legacy organisation/product edit routes redirect
into the new review workflow for sandbox records.

## Production Configuration

Use the production Compose stack with its Django, Celery worker, Celery beat,
PostgreSQL and Redis services. Supply normal Django HTTPS, host, database
and email settings plus the following secrets in deployment configuration:

| Setting | Requirement |
| --- | --- |
| `SANDBOX_CREDENTIAL_KEY` | Dedicated Fernet key, kept in a secret manager, separate from Django's secret key. Back it up with appropriate access controls. |
| `SANDBOX_CREDENTIAL_PROVIDER` | Dotted Python callable implementing real gateway provisioning. Required before issuing any real credentials. |
| `SANDBOX_GATEWAY_URL` | Approved sandbox gateway endpoint, if using the local demo provider. |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Registered Cloudflare Turnstile site keys for the deployed hostname. |
| `DJANGO_AWS_ACCESS_KEY_ID`, `DJANGO_AWS_SECRET_ACCESS_KEY` | Credentials scoped to the upload bucket. |
| `DJANGO_AWS_STORAGE_BUCKET_NAME`, `DJANGO_AWS_S3_REGION_NAME` | Private S3 bucket and region. |
| `DJANGO_AWS_S3_ENDPOINT_URL` | Optional S3-compatible endpoint. Local settings use MinIO. |

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
