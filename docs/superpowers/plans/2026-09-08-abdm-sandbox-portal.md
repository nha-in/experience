# ABDM Developer Sandbox Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Care Experience Hub codebase into the ABDM Developer Sandbox Portal: integrators onboard, register products, get sandbox credentials and file per-milestone exit requests; NHA reviewers verify organisations, register products and decide exit requests from a queue.

**Architecture:** One new Django app, `ohc_experience.abdm`, owns products, credentials, compliance records, review items, queries, history and the audit log, with a code-defined track/milestone registry and a `services.py` write boundary (the same shape as `experiences`, `support` and `organisations`). Integrator screens live under `/onboarding/` and `/products/` in the existing vendor shell; reviewer screens live under `/assess/` in the existing console shell. The organisations, users, support and events apps are extended in place.

**Tech Stack:** Django 6, django-htmx, django-allauth, Celery, Tailwind 4 + careui component classes (`theme/`), pytest + factory-boy, PostgreSQL, S3/MinIO media, `cryptography` (Fernet) for the client secret.

**Spec:** `docs/superpowers/specs/2026-09-08-abdm-sandbox-portal-design.md`

## Global Constraints

- Every mutation keeps an ordinary `method="post"` + `action=` + `{% csrf_token %}` path; htmx is layered on top (`hx-post` … `hx-target`), never instead. Partial endpoints answer htmx with a fragment and everyone else with POST → redirect → flash.
- Colour only through careui tokens/classes (`ui-card`, `ui-badge--*`, `bg-card`, `text-muted-foreground`…); status is always label + dot/badge, never colour alone.
- Views never write state: services do. Every write on Organisation, Product, ComplianceRecord and ReviewItem goes through `abdm.services` and records an `AuditLog` row (actor, timestamp, diff) plus, where the spec says so, a `ReviewHistory` row.
- Files: certificate/report PDF only, ≤ 10 MB; logo images only; downloads stream through a permission-checked view, never a raw storage path.
- Integrators only ever see their own organisation's rows (`OrganisationMixin` + `for_organisation` querysets); reviewers are `is_ohc_team` (`OhcConsoleMixin`); admins are `is_superuser`.
- `TIME_ZONE = "Asia/Kolkata"`; the UI states that the sandbox holds synthetic data only.
- No git commits in this session (the user has not asked for them); each task ends with the full suite green: `DATABASE_URL=postgres://localhost:5432/ohc_experience_dev USE_DOCKER=no uv run pytest -q -p no:sugar`.
- Lint gates: `uv run ruff check --fix . && uv run ruff format .` and `uv run djlint ohc_experience/templates --reformat --quiet` before the final run; `uv run python manage.py makemigrations --check`.

---

## File structure

**New app `ohc_experience/abdm/`**

| File | Responsibility |
|---|---|
| `apps.py` | `AbdmConfig`, name `ohc_experience.abdm`. |
| `tracks.py` | Frozen dataclasses `Track` / `Milestone`, the `TRACKS` tuple, `get_track`, `get_milestone`, `canonical_key`, `milestone_choices_by_track`, `previous_milestone`. No DB. |
| `models.py` | `Product`, `Credential`, `ComplianceRecord`, `ReviewItem`, `ReviewQuery`, `ReviewHistory`, `AuditLog` + TextChoices + querysets. Status variants live on the models. |
| `crypto.py` | `encrypt_secret(str) -> str`, `decrypt_secret(str) -> str` (Fernet, key derived from `SECRET_KEY`). |
| `references.py` | `next_sandbox_id()` (`SBX-YYYY-NNNNN`), `next_review_reference()` (`REV-YYYY-NNNNN`). |
| `audit.py` | `snapshot(instance) -> dict`, `record_audit(actor, instance, action, before=None)`. |
| `services.py` | Every write (organisation submission, product register/update, drafts, exit request, withdraw, replies, review decisions, queries, credentials). Raises `PermissionDenied` / `ValidationError`. |
| `selectors.py` | Reads: `products_for(org)`, `queue_items(filters)`, `dashboard_stats()`, `activity_for_product(product)`, `open_queries_for(org)`. |
| `notifications.py` | `notify_*` functions rendering `templates/abdm/email/*.txt`; recipients helpers. |
| `callback.py` | `probe_callback(url) -> CallbackResult` (stdlib urllib, 5 s timeout). |
| `tasks.py` | Celery `run_callback_checks` (beat every 15 min). |
| `forms.py` | `ProductForm`, `ExitRequestForm`, `CallbackUrlsForm`, `QueryReplyForm`, `ApproveForm`, `SendBackForm`, `RaiseQueryForm`, `AssignForm`, `QueueFilterForm`. |
| `views.py` | Integrator views (`products` namespace) + onboarding product step + document download. |
| `assess_views.py` | Reviewer views (`assess` namespace). |
| `urls.py`, `assess_urls.py` | URL confs. |
| `context_processors.py` | `current_product` for the sidebar. |
| `admin.py` | Model admins (history/audit read-only). |
| `management/commands/seed_abdm_demo.py` | Demo integrator + reviewer + admin, two products, items in every state. |
| `tests/` | `factories.py`, `test_tracks.py`, `test_services.py`, `test_credentials.py`, `test_integrator_views.py`, `test_assess_views.py`, `test_seed.py`. |

**Templates (new)** under `ohc_experience/templates/`:

- `layouts/onboarding.html` — bare header + step indicator (Account ✓ · Organisation · Product).
- `organisations/onboarding.html` (rewritten to extend `layouts/onboarding.html`), `organisations/partials/organisation_form.html` (rewritten for the new sections), `organisations/partials/verification_card.html`, `organisations/partials/query_list.html`.
- `abdm/onboarding_product.html`, `abdm/product_form.html`, `abdm/partials/product_form.html`, `abdm/partials/milestone_checkboxes.html`.
- `abdm/product_overview.html`, `abdm/partials/track_status_list.html`, `abdm/partials/activity_feed.html`.
- `abdm/credentials.html`, `abdm/partials/credentials_card.html`, `abdm/partials/callback_card.html`, `abdm/partials/secret_reveal.html`.
- `abdm/track.html`, `abdm/partials/milestone_tiles.html`, `abdm/partials/milestone_detail.html`, `abdm/partials/exit_form.html`, `abdm/partials/exit_summary.html`, `abdm/partials/query_thread.html`.
- `abdm/assess/dashboard.html`, `abdm/assess/queue.html`, `abdm/assess/partials/queue_results.html`, `abdm/assess/review_detail.html`, `abdm/assess/partials/submitted_form.html`, `abdm/assess/partials/decision_panel.html`, `abdm/assess/partials/queries_card.html`, `abdm/assess/partials/history_card.html`, `abdm/assess/partials/review_workspace.html`.
- `abdm/email/*.txt` (subject + body pairs).

**Modified**

- `config/settings/base.py` — `TIME_ZONE`, app, context processor, `ABDM_*` settings, beat schedule; `pyproject.toml` — `cryptography`.
- `config/urls.py` — mount `products`, `assess`, `/signup/`, `/onboarding/`.
- `ohc_experience/organisations/models.py|forms.py|views.py|urls.py|admin.py` + migration `0004`.
- `ohc_experience/users/forms.py`, `templates/account/signup.html`, `users/views.py` (captcha).
- `ohc_experience/pages/views.py|urls.py` (post-login routing, dashboard redirect), `templates/pages/home.html`.
- `templates/base.html`, `components/brand.html`, `components/app_nav.html`, `ohc/partials/console_nav.html`, `ohc/partials/console_brand.html`, `ohc/partials/organisation_register.html`, `ohc/views.py|urls.py|forms.py`.
- `ohc_experience/support/*` (+ migration 0002), `ohc_experience/events/*` (+ migration 0002) and their templates.
- `components/file_upload_field.html` + `experiences/models.py` (`download_url` property) so the upload widget works for any attachment-like object.
- `README.md`, `docs/`.

---

## Interfaces (shared by every task)

### Tracks registry (`abdm/tracks.py`)

```python
@dataclass(frozen=True)
class Milestone:
    code: str            # "M1"
    name: str            # "ABHA and identity"
    track_code: str      # owning track, "HI-CM"
    order: int
    description: str
    docs_url: str
    alias_of: tuple[str, str] | None = None   # ("HI-CM", "M1") when this tile is another track's record

    @property
    def key(self) -> str: ...            # "PHR:M1" (as shown/selected)
    @property
    def canonical(self) -> tuple[str, str]: ...  # alias_of or (track_code, code)
    @property
    def canonical_key(self) -> str: ...  # "HI-CM:M1"

@dataclass(frozen=True)
class Track:
    code: str; name: str; description: str; docs_url: str
    milestones: tuple[Milestone, ...]

TRACKS: tuple[Track, ...]            # HI-CM, UHI, NHCX, PHR, HealthLocker (sidebar order)
TRACK_CHOICES: list[tuple[str, str]]
def get_track(code) -> Track            # KeyError on unknown
def get_milestone(track_code, code) -> Milestone
def parse_key(key) -> tuple[str, str]   # "HI-CM:M2" -> ("HI-CM", "M2")
def canonical_key(key) -> str
def milestone_label(track_code, code) -> str   # "M2 · HIP services"
def previous_milestone(track: Track, milestone: Milestone) -> Milestone | None
def validate_selection(keys: Iterable[str]) -> tuple[set[str], set[str]]
    # returns (canonical_keys, applied_track_codes); raises ValidationError when a milestone is
    # selected without every earlier milestone of its track (after alias resolution)
```

### Models (`abdm/models.py`)

```python
class Product(models.Model):
    class Category(TextChoices): HMIS, LMIS, PHR_APP, HEALTH_LOCKER, PAYER_TPA, OTHER
    class SolutionType(TextChoices): CLINICAL_HMIS, EUA, HEALTH_LOCKER
    class RegistrationStatus(TextChoices): PENDING, REGISTERED, SENT_BACK
    organisation FK -> organisations.Organisation (related_name="products")
    sandbox_id CharField(unique)   # SBX-YYYY-NNNNN
    name, description, category, solution_type
    applied_tracks JSONField(list[str]); applied_milestones JSONField(list[str] canonical keys)
    registration_status; registered_on; registered_by; sent_back_reason
    created_by; created_at; updated_at
    # helpers: get_absolute_url -> products:overview; status_variant; track_codes -> [Track];
    #          records_for_track(track) -> list[ComplianceRecord|None] in milestone order

class Credential(models.Model):
    product OneToOne (related_name="credential")
    client_id CharField(unique); secret_encrypted TextField
    gateway_base_url URLField; callback_url URLField(blank); bridge_url URLField(blank)
    issued_on DateTime; rotation_due Date; rotated_on DateTime(null); revoked_on DateTime(null)
    callback_status_code SmallInt(null); callback_latency_ms Int(null); callback_checked_at(null)
    callback_error CharField(blank); callback_failure_streak PositiveSmallInt
    # helpers: secret (decrypt), is_active, callback_variant ("success"/"destructive"/"neutral"), callback_label

class ComplianceRecord(models.Model):
    class Status(TextChoices): LOCKED, OPEN, IN_PROGRESS, UNDER_REVIEW, QUERY_RAISED, APPROVED
    product FK (related_name="compliance_records"); track_code; milestone_code; status
    start_date, end_date, demo_date (Date null); wasa_agency CharField(blank); wasa_date Date null
    functional_certificate FileField(upload_to="abdm/compliance/%Y/%m/", blank)
    functional_report FileField(..., blank)
    submitted_on DateTime null; resubmission_count PositiveSmallInt
    approved_on Date null; approved_by FK null; decision_note TextField(blank)
    sent_back_on DateTime null; sent_back_by FK null; sent_back_reason TextField(blank)
    created_at; updated_at; unique (product, track_code, milestone_code)
    # helpers: milestone (Milestone), track (Track), label, key, is_editable, is_complete,
    #          missing_fields -> list[str], status_variant, review_item (reverse OneToOne, may be None)

class ReviewItem(models.Model):
    class Type(TextChoices): EXIT_REQUEST, ORGANISATION_VERIFICATION, PRODUCT_REGISTRATION
    class Status(TextChoices): NEW, IN_REVIEW, QUERY_RAISED, APPROVED, SENT_BACK, WITHDRAWN
    reference CharField(unique)  # REV-YYYY-NNNNN
    type; organisation FK (related_name="review_items"); product FK null; compliance OneToOne null
    status; submitted_on DateTime; resubmission_count
    assignee FK null (limit is_ohc_team); assigned_by FK null
    decided_on Date null; decided_by FK null; decision_note TextField(blank)
    created_at; updated_at
    # helpers: subject_label, title, track_code, milestone_code, age_days, is_open,
    #          status_variant, get_absolute_url -> assess:review, open_query_count
    # querysets: open(), for_reviewer(user), of_type(t), ordered by submitted_on (oldest first)

class ReviewQuery(models.Model):
    class Status(TextChoices): OPEN, ANSWERED, RESOLVED
    item FK (related_name="queries"); field_key CharField ("form" = whole form); field_label
    question TextField; raised_by FK; raised_at
    status; reply TextField(blank); replied_by FK null; replied_at null
    resolved_at null; resolved_by FK null

class ReviewHistory(models.Model):   # immutable; save() refuses updates
    class Kind(TextChoices): SUBMITTED, RESUBMITTED, STARTED, ASSIGNED, QUERY_RAISED,
        QUERY_ANSWERED, QUERY_RESOLVED, APPROVED, SENT_BACK, WITHDRAWN, MILESTONES_CHANGED, NOTE
    item FK (related_name="history"); actor FK null; kind; title; description TextField(blank)
    payload JSONField; created_at   # ordering: -created_at, -pk

class AuditLog(models.Model):        # immutable
    actor FK null; model_label CharField; object_pk CharField; object_repr CharField
    action CharField; diff JSONField; created_at
```

### Organisation additions (`organisations/models.py`)

```python
class EntityType(TextChoices): PRIVATE_COMPANY, GOVERNMENT_BODY, SOLE_PROPRIETORSHIP, TRUST_OR_SOCIETY, SECTION_8, LLP
class Category(TextChoices): INDIA_ENTITY, FOREIGN_WITH_INDIAN_SUBSIDIARY, ACADEMIC
class VerificationDocumentType(TextChoices): PAN, GSTIN, CIN
Organisation.VerificationStatus: PENDING="pending", VERIFIED="verified", SENT_BACK="sent_back"  (rejected → sent_back in migration)
new fields: description, entity_type, category, logo (ImageField), registered_address, pincode, district,
            verification_document_type, verification_document_number, verification_document (FileField),
            verification_submitted_at, verified_by FK null, verification_reason TextField(blank)
helpers: is_pending, is_sent_back, has_submitted_details, verification_review_item (or None),
         details_complete -> bool, missing_details -> list[str]
set_verification(status, *, actor=None, reason="") keeps working (records verified_by / reason)
```

### Services (`abdm/services.py`) — exact signatures

```python
submit_organisation_for_verification(*, organisation, user) -> ReviewItem
register_product(*, organisation, user, data: dict) -> Product          # data: name, description, category, solution_type, milestones (keys)
update_product(*, product, user, data: dict) -> Product
sync_milestone_locks(product) -> None
save_exit_draft(*, record, user, form) -> ComplianceRecord              # form is a valid ExitRequestForm bound with files
request_exit(*, record, user) -> ReviewItem
withdraw_exit(*, record, user) -> None
reply_to_query(*, query, user, reply: str) -> ReviewQuery
start_review(*, item, user) -> None
assign_reviewer(*, item, actor, assignee) -> None                        # actor must be superuser
raise_query(*, item, user, field_key: str, field_label: str, question: str) -> ReviewQuery
resolve_query(*, query, user) -> None
approve(*, item, user, approved_on: date, note: str = "") -> None
send_back(*, item, user, reason: str) -> None
issue_credentials(*, product, actor=None) -> Credential | None           # None unless org verified & no active credential
rotate_credentials(*, product, user) -> Credential
revoke_credentials(*, product, user) -> None
update_callback_urls(*, product, user, callback_url: str, bridge_url: str) -> Credential
check_callback(*, product, actor=None) -> Credential                      # stores result, bumps streak, emails on 3rd failure
reveal_secret(*, product, user) -> str                                    # audits + rate-limits (cache), raises PermissionDenied when over limit
```

### URL names

`products:` `onboarding-product` (/onboarding/product/), `new` (/products/new/), `overview` (/products/<sbx>/), `edit`, `credentials`, `credentials-reveal`, `credentials-rotate`, `credentials-revoke`, `credentials-urls`, `credentials-test`, `track` (/products/<sbx>/tracks/<track>/), `milestone-draft`, `milestone-request`, `milestone-withdraw` (/products/<sbx>/tracks/<track>/<milestone>/{draft,request,withdraw}/), `query-reply` (/queries/<pk>/reply/), `document` (/files/<kind>/<pk>/<field>/).

`assess:` `dashboard`, `queue`, `review` (/assess/review/<reference>/), `review-start`, `review-assign`, `review-query`, `review-approve`, `review-send-back`, `query-resolve` (/assess/review/<reference>/queries/<pk>/resolve/).

`organisations:` `onboarding` (/onboarding/organisation/), `submit-verification` (POST), `detail` (settings page, unchanged name).

---

## Task 1: Foundations — settings, tracks registry, models, migrations

**Files:** create `abdm/{__init__,apps,tracks,models,crypto,references,audit,admin}.py`, `abdm/tests/{__init__,factories,test_tracks,test_models}.py`, `abdm/migrations/0001_initial.py` (generated); modify `config/settings/base.py`, `pyproject.toml` (+cryptography via `uv add cryptography`), `organisations/models.py` (+ migration `0004_abdm_organisation_details.py` with a RunPython that maps `rejected` → `sent_back`), `organisations/admin.py`, `users/models.py` (`role` property).

- [ ] Write `test_tracks.py`: TRACKS order is HI-CM, UHI, NHCX, PHR, HealthLocker; HI-CM has M1..M4 with M4 named "HFR registration"; PHR's first milestone is an alias of HI-CM:M1; NHCX has no milestones; `validate_selection({"HI-CM:M2"})` raises; `validate_selection({"PHR:M1","PHR:PHR1"})` returns canonical `{"HI-CM:M1","PHR:PHR1"}` and tracks `{"PHR"}`.
- [ ] Write `test_models.py`: sandbox id format and yearly sequence; review reference format; `ComplianceRecord.is_complete/missing_fields`; `ReviewHistory` refuses updates; `Credential.secret` round-trips through `crypto`.
- [ ] Implement, `makemigrations abdm organisations`, run tests.

## Task 2: Organisation details, onboarding step 2, verification submission

**Files:** modify `organisations/forms.py` (OrganisationProfileForm → the spec's three sections; `OrganisationSignupType`), `organisations/views.py` (OnboardingView GET/POST at `/onboarding/organisation/`, `SubmitVerificationView`, settings detail view with queries + resubmit), `organisations/urls.py`, templates `layouts/onboarding.html`, `organisations/onboarding.html`, `organisations/partials/organisation_form.html`, `organisations/partials/verification_card.html`, `organisations/organisation_detail.html`, `ohc/partials/organisation_register.html` (+ `ohc/views.py` drops the status-select endpoint), `abdm/services.py::submit_organisation_for_verification`, `abdm/notifications.py` (org verified / sent back stubs used later), `pages/views.py::resolve_post_login_destination`.

- [ ] Tests (`organisations/tests/test_views.py` rewrite of onboarding cases; `abdm/tests/test_services.py::TestOrganisationVerification`): saving details keeps status pending and sets `verification_submitted_at`; submitting creates one `organisation_verification` ReviewItem with a SUBMITTED history row; resubmitting after `sent_back` reuses the item (status IN_REVIEW, resubmission_count 1, RESUBMITTED history) and flips the org back to pending; developer role cannot edit; console page renders the new fields and links to the review item.
- [ ] Implement; update `ohc/tests` that used the old verification endpoint; run suite.

## Task 3: Sign-up, captcha, branding, post-login routing

**Files:** `users/forms.py` (fields: name, organisation, organisation_type, email, password1/2, captcha), `users/captcha.py` (`issue_challenge(session) -> str question`, `verify(session, answer) -> bool`), `users/views.py`, `templates/account/signup.html`, `templates/pages/home.html`, `templates/base.html` (title), `components/brand.html`, `ohc/partials/console_brand.html`, `config/urls.py` (`/signup/` → account_signup), `pages/views.py` (routing: no details → onboarding org; no product → onboarding product; else product overview; OHC → assess dashboard), `pages/urls.py` (`dashboard` → redirect view).

- [ ] Tests: signup creates user + org with `entity_type` mapped from the three-way choice; wrong captcha answer rejected; mobile number no longer required; post-login destinations; `dashboard` redirects to `products:overview` when a product exists.
- [ ] Implement; update `users/tests`, `pages/tests`; run suite.

## Task 4: Products — register/edit, compliance records, overview, sidebar

**Files:** `abdm/forms.py::ProductForm`, `abdm/services.py::{register_product,update_product,sync_milestone_locks}`, `abdm/selectors.py::{products_for,activity_for_product}`, `abdm/views.py` (`ProductMixin`, `ProductOnboardingView`, `ProductCreateView`, `ProductEditView`, `ProductOverviewView`), `abdm/urls.py`, `abdm/context_processors.py`, templates listed above, `components/app_nav.html` (Overview · Credentials · Tracks · Events · Support · Edit product · Settings), `config/urls.py`.

- [ ] Tests: registering creates SBX id, one record per canonical milestone (first per track OPEN, others LOCKED; PHR:M1 shares HI-CM:M1), a `product_registration` item, an AuditLog row; selecting M2 without M1 fails; editing cannot drop an approved milestone; adding a milestone creates a record and a MILESTONES_CHANGED history row + email to reviewers; overview renders track pills; the sidebar lists the five tracks; another org's SBX id 404s.
- [ ] Implement; run suite.

## Task 5: Credentials

**Files:** `abdm/callback.py`, `abdm/services.py` (issue/rotate/revoke/update urls/check/reveal), `abdm/tasks.py`, `abdm/views.py` (`CredentialsView` + 5 POST views), templates `abdm/credentials.html` + partials, settings (`ABDM_SANDBOX_GATEWAY_URL`, `ABDM_SECRET_REVEAL_LIMIT`, beat schedule), `abdm/notifications.py::notify_callback_failing`.

- [ ] Tests: credentials are issued when the org is verified (approve path) and on product registration for an already-verified org, not before; rotate replaces the secret and pushes `rotation_due`; revoke deactivates; reveal returns the secret, writes an audit row, and the 6th reveal in 10 minutes is refused; `check_callback` with a stub probe stores status/latency and emails on the third consecutive failure; the credentials page masks the secret and the reviewer detail never contains it.
- [ ] Implement; run suite.

## Task 6: Tracks and exit requests

**Files:** `abdm/forms.py::{ExitRequestForm,QueryReplyForm}`, `abdm/services.py::{save_exit_draft,request_exit,withdraw_exit,reply_to_query}`, `abdm/views.py` (`TrackView`, `MilestoneDraftView`, `MilestoneRequestExitView`, `MilestoneWithdrawView`, `QueryReplyView`, `DocumentDownloadView`), templates `abdm/track.html` + partials, `components/file_upload_field.html` (+ `ApplicationAttachment.download_url`).

- [ ] Tests: draft moves OPEN→IN_PROGRESS and keeps files; request refused while incomplete (reason text lists missing fields) and moves to UNDER_REVIEW creating an exit_request item (email to reviewers); withdraw returns to IN_PROGRESS, marks the item WITHDRAWN and closes open queries; a locked milestone shows the unlock message; the PHR track page shows HI-CM M1's record; NHCX renders the "no milestones published" state; a not-applied track renders the empty state with Edit product; reply to a query moves it to ANSWERED and the record back to UNDER_REVIEW; downloads are scoped to the org and allowed for reviewers.
- [ ] Implement; run suite.

## Task 7: Reviewer queue and review detail

**Files:** `abdm/forms.py::{ApproveForm,SendBackForm,RaiseQueryForm,AssignForm,QueueFilterForm}`, `abdm/services.py::{start_review,assign_reviewer,raise_query,resolve_query,approve,send_back}`, `abdm/selectors.py::queue_items`, `abdm/assess_views.py`, `abdm/assess_urls.py`, templates `abdm/assess/queue.html`, `review_detail.html` + partials, `ohc/partials/console_nav.html` (Dashboard · Review queue · Tickets · Organisations · Events), `abdm/notifications.py` (approved, sent back, query raised, query answered, product registered).

- [ ] Tests: the gate (anonymous → login, vendor → 403, reviewer → 200) for every assess route; queue sorts oldest first and the chips filter by type and "Mine"; approve on an exit item sets APPROVED on record + item, stamps approved_on/by, unlocks the next milestone and emails the integrator; approve on organisation verification sets `verified` and issues credentials for its products; approve on product registration sets `registered`; send back requires a reason and unlocks the record (IN_PROGRESS) / org (sent_back) / product (sent_back); raise query pauses the item and record and emails; resolve query returns the item to IN_REVIEW; only a superuser can assign; the submitted-form partial shows every field with a Query action and the "Query open" tag.
- [ ] Implement; run suite.

## Task 8: Reviewer dashboard

**Files:** `abdm/selectors.py::dashboard_stats`, `abdm/assess_views.py::DashboardView`, `templates/abdm/assess/dashboard.html`.

- [ ] Tests: counts by status; median time to decision; queries awaiting integrator; approved this month by milestone; five oldest with >10 days flagged; 8-week decision series; ageing buckets; reviewer load includes "Unassigned".
- [ ] Implement; run suite.

## Task 9: Support and events extensions

**Files:** `support/models.py` (+ `product` FK, `track`, `TicketMessage.attachment`, `sla_label`), migration `0002`, `support/forms.py`, `support/views.py` (attachment download, product/track choices), templates; `ohc/partials/ticket_workspace.html`; `events/models.py` (+ `EventRegistration`, `materials_url`, `recording_url`), migration `0002`, `events/views.py` (register/unregister POST, filter chips), `events/tasks.py::send_event_reminders`, templates; `support`/`events` notifications (ticket replies, registration confirmation).

- [ ] Tests: ticket create with product + track shows them in the rail; attachment download is org-scoped; reply emails the other party; event register/unregister toggles and confirmation mail sent; "Registered" chip filter; past event shows materials links; reminder task emails registrants of events starting within 24 h once.
- [ ] Implement; run suite.

## Task 10: Demo seed, docs, CSS build, final gates

**Files:** `abdm/management/commands/seed_abdm_demo.py`, `abdm/tests/test_seed.py`, `README.md`, `docs/`, `theme/static/css/dist/styles.css` (rebuilt).

- [x] Seeder is idempotent (`seed_abdm_demo`, `--fresh`, `--password`) and creates: the certification desk (two reviewers, one admin), Medibase Health Systems (verified) with Medibase HMIS 4.2 mid-way through HI-CM, six more organisations whose items fill the queue in every state, seven events, three tickets.
- [x] `uv run python manage.py tailwind build`, ruff, djlint, `makemigrations --check`, full suite (650 passed, 1 xfailed).

## Task 11: Match the prototype (`ABDM Sandbox Portal v3.dc.html`)

Done after the mockups arrived: every screen re-drawn to the prototype — the
care-logo brand and "ABDM developer sandbox" caption, the 232px rail with the
product card, TRACKS / PROGRAMME / PRODUCT sections and counters, the 50px top
bar (breadcrumb, "Sandbox environment" pill, initials), the stepped onboarding
header, single-card forms with section rules, the overview's tracks/activity/
organisation/credentials/events cards, the credentials cards with the note
underneath, milestone tiles and the exit-request card in all six states, the
events list with its rail, the ticket thread with its rail, and the reviewer
dashboard, queue and review detail with the segmented decision switch.

Deliberate departures from the prototype, all from the design doc's answers:
M4 is "HFR registration" (not "Scan and share"); no "decision within 10 working
days" promise (no decision SLA); the NHCX empty state points at Events rather
than a specific hackathon; times print on the 24-hour clock with the zone.

---

## Self-review

- Spec coverage: 5.1 → Task 3; 5.2 → Task 2; 5.3/5.4 → Task 4; 5.5 → Task 5; 5.6 → Task 6; 5.7/5.8 → Task 9; 5.9 → Task 8; 5.10/5.11 → Task 7; §6 permissions → mixins in Tasks 4/7; §7 notifications → Tasks 2/4/5/7/9; §8 audit/secrets/callback/IST → Tasks 1/5; §10 answers → tracks registry (M4, PHR M1 alias), assignment (Task 7), withdraw-to-edit (Task 6).
- Names used across tasks match the interface block above.
