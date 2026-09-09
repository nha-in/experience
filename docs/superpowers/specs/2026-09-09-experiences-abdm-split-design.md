# Experiences / ABDM split — Design

9 September 2026. Approved in chat: clean split now, drop the dormant
production-access flow and everything that only existed for it. The larger
idea, re-expressing the sandbox portal on the engine so `abdm` needs no
models, is a separate project and is out of scope here.

## 1. Purpose

Make the boundary between the two apps mean one thing:

- `ohc_experience.experiences` is the abstraction: the code-defined experience
  engine, self-contained, with nothing ABDM in it and tests that need no ABDM
  code.
- `ohc_experience.abdm` is the ABDM sandbox portal: every ABDM-specific model,
  screen, catalogue and email. It stays a Django app because it owns models,
  migrations, template tags and management commands.
- Plumbing that both of them and the other apps share (mail delivery, upload
  validation, the file-widget contract, a status-dot colour) lives where it is
  obvious, and no app reaches into `abdm` for something generic.

## 2. What is wrong today

Two ABDM implementations that never meet:

1. `experiences/abdm/` — the "ABDM production access" application type
   (definition + forms) registered from `ExperiencesConfig.ready()`, seeded by
   `seed_experience_demo`, and the only thing the engine's tests run against.
   Routable at `/applications/` and `/ohc/applications/` but linked from no
   nav or template; the 8 September spec sidelined it on purpose.
2. `abdm/` — the sandbox portal (products, credentials, compliance records,
   review queue). The product. Uses none of the engine.

Misplaced code:

- The engine's base form `ExperienceForm` (every registered form must subclass
  it; `definitions.build_form` passes `experience_context` and
  `existing_files`, `services` reads `removed_file_ids`) lives in
  `experiences/abdm/forms.py` beside a second copy of `validate_upload`.
- `abdm/uploads.py` holds the generic upload validator, size limits and the
  `ModelFileFormMixin` that gives a ModelForm the engine's file-widget shape;
  `organisations` and `support` import them from `abdm`.
- `abdm/notifications.py` holds `deliver`, `absolute_url` and the recipient
  helpers; `support` and `events` import them from `abdm`.
- `abdm/templatetags/abdm_extras.py` holds `dot_class`, which the shared
  `components/status_dot.html` loads.

## 3. Target layout

```
ohc_experience/
  core/                      new, plain package (no models, not an app)
    __init__.py
    mail.py                  deliver(), absolute_url(), team_recipients()
  experiences/               the engine, self-contained
    forms.py                 + ExperienceForm
    uploads.py               new: limits, validate_upload(s), ExistingFile,
                             existing_file(), ModelFileFormMixin
    apps.py                  ready() registers nothing
    README.md                rewritten around the sample definition
    tests/
      conftest.py            new: registers the sample definition
      sample.py              new: SampleExperience — forms, definition, actions
      test_definitions.py    new: registry + definition contract (no DB)
      test_services.py       ported from test_engine.py
      test_views.py          ported from the view half of test_seed_and_views.py
    abdm/                    deleted
    management/              deleted (only seed_experience_demo lived there)
  abdm/
    uploads.py               slimmed: document_url(), PortalFileFormMixin
    notifications.py         uses core.mail + organisations recipients
    selectors.py             uses uploads.document_url (drops its private copy)
    templatetags/abdm_extras.py   drops dot_class
  organisations/
    selectors.py             + notification_recipients(organisation)
    forms.py                 imports from experiences.uploads / abdm.uploads
  support/, events/          import mail helpers from core / organisations
  conftest.py                mail_outage patches core.mail.send_mail
theme/templatetags/careui.py + dot_class
ohc_experience/templates/components/status_dot.html   loads careui
```

No model changes. No migrations. No settings changes (`ABDM_REVIEW_INBOX`
keeps its name; it is deployment config and the product is the ABDM portal).

## 4. Changes by area

### 4.1 The engine (`experiences`)

- `forms.py` gains `ExperienceForm` (moved verbatim from
  `experiences/abdm/forms.py`): the constructor that accepts
  `experience_context` and `existing_files`, the posted-removal parsing,
  `retained_existing_files`, the multiple-file min/max check in `clean`, and
  `require_upload`.
- New `uploads.py`, the single home of upload plumbing:
  - `MB`, `PDF`, `IMAGES`, `DOCUMENTS`, `PDF_MAX_MB`, `IMAGE_MAX_MB`.
  - `validate_upload(upload, *, extensions, max_mb)` — one implementation,
    accepting `None` and `False` (a cleared FileField), any iterable of
    extensions.
  - `validate_uploads(uploads, *, extensions, max_mb)` for `MultipleFileField`.
  - `ExistingFile` dataclass and `existing_file(instance, field_name, *,
    download_url)` — the shape `components/file_upload_field.html` draws.
  - `ModelFileFormMixin` with one hook, `download_url(field_name) -> str`,
    that a subclass overrides. Everything else (existing-file presentation,
    removal parsing, `new_upload`, `has_file`, clearing on removal) is generic
    and moves as is.
- `apps.py`: `ready()` no longer imports anything. Registration is the job of
  the app that owns a definition.
- Delete `experiences/abdm/` and `experiences/management/`.
- Delete `tests/test_engine.py` and `tests/test_seed_and_views.py`; replace
  them as in §5.
- `README.md`: drop the ABDM example and the seed command; point "Add an
  experience" at `tests/sample.py` as the reference implementation.

### 4.2 `core/mail.py`

Moved from `abdm/notifications.py`, unchanged in behaviour:

- `deliver(subject, body, recipients)` — sends, logs a failure, never raises.
- `absolute_url(path)` — current Site domain, `http` under DEBUG else `https`.
- `team_recipients()` — `settings.ABDM_REVIEW_INBOX` when set, else every
  active OHC team member's email. This is the item-less half of today's
  `reviewer_recipients`.

### 4.3 `organisations`

- `selectors.py` gains `notification_recipients(organisation)`: emails of the
  owner/admin members plus the technical contact, deduplicated and sorted.
  This is today's `integrator_recipients`, moved to the app that knows what a
  manager is.
- `forms.py`: `validate_upload` and the limits come from
  `experiences.uploads`; `OrganisationProfileForm` mixes in
  `abdm.uploads.PortalFileFormMixin` (its documents are served by the portal's
  download view, so that dependency is real and stays).

### 4.4 `abdm`

- `uploads.py` keeps only what is portal-specific: `document_url(kind, pk,
  field)` reversing `products:document`, and `PortalFileFormMixin`
  (`download_kind` + the `download_url` hook). `selectors.py` uses
  `document_url` instead of its private `_document_url`.
- `notifications.py`: imports `deliver`, `absolute_url`, `team_recipients`
  from `core.mail` and `notification_recipients` from `organisations`.
  Keeps `reviewer_recipients(item=None)` as the assignee-aware wrapper and
  every `notify_*` function.
- `forms.py`: imports limits and `validate_upload` from `experiences.uploads`
  and the mixin from `.uploads`.
- `templatetags/abdm_extras.py`: `dot_class` removed; `dict_get`, `pct_of`,
  `file_basename` stay (only ABDM templates use them).

### 4.5 `support` and `events`

- `support/forms.py`: `DOCUMENTS`, `PDF_MAX_MB`, `validate_upload` from
  `experiences.uploads`. `Product` and `TRACK_CHOICES` keep coming from `abdm`;
  tickets are product-aware by spec.
- `support/notifications.py` and `events/notifications.py`: `deliver`,
  `absolute_url`, `team_recipients` from `core.mail`;
  `notification_recipients` from `organisations.selectors`.

### 4.6 Theme

- `theme/templatetags/careui.py` gains the `dot_class` filter (badge variant →
  Tailwind colour class).
- `components/status_dot.html` loads `careui`. Any ABDM template that used
  `dot_class` directly loads `careui` for it.

### 4.7 Test fixtures

- `ohc_experience/conftest.py`: `mail_outage` patches
  `ohc_experience.core.mail.send_mail`.

## 5. Engine tests without ABDM

`experiences/tests/sample.py` defines `SampleExperience`, small but shaped to
exercise every engine feature the dropped tests covered:

- Forms (all `ExperienceForm`): `ProfileForm`; `ScopeForm` with a boolean that
  switches a later form on; `ExtraForm` (applicable only when switched on);
  `EvidenceForm` with a single PDF `FileField`, a `MultipleFileField`
  (1–3 PDFs), `issued_on`/`expires_on`; `DeclarationForm`; action forms for
  raise-query, approve and reject.
- Definitions: `profile` (updatable), `scope` (depends on profile), `extra`
  (conditional), `evidence` (repeatable, `valid_until_field="expires_on"`,
  30-day renewal window, editable while approved, one form action
  `verify_evidence`), `declaration` (locked once complete).
- Actions: submit, ask review team, start review, raise query, approve, reject
  — same statuses and permission keys as before, so the engine's views and
  services see the same state machine.
- Roles: `applicant_owner`, `applicant_contributor`, `applicant_viewer`,
  `reviewer`, `decision_maker`.

`experiences/tests/conftest.py` imports `sample`, which registers the
definition idempotently. Nothing outside the tests registers it, so the engine
ships with an empty registry until a real app registers something.

Test modules:

- `test_definitions.py` (no DB): registry rejects duplicate keys, unknown
  permissions and dependencies; form visibility follows dependencies and
  applicability; renewal-due logic; availability messages.
- `test_services.py`: ported from `test_engine.py` — create with the owner
  grant, effective permissions and direct-permission union, form submission
  revisions and attachments (add, remove, keep), repeatable submissions and
  renewals, form actions, application actions incl. queries, access grants and
  the stranded-owner rule, progress.
- `test_views.py`: ported from the view half of `test_seed_and_views.py` —
  the applicant list (with no definitions registered too), start, detail, form
  workspace with uploads and removals, action and form-action workspaces,
  query threads, access management, attachment download scoping; the console
  list, detail, action, query-resolve and access screens. Seed-command tests
  are dropped with the command.

## 6. Deleted

- `ohc_experience/experiences/abdm/` (definition, forms).
- `ohc_experience/experiences/management/` (`seed_experience_demo`).
- `ohc_experience/experiences/tests/test_engine.py`,
  `test_seed_and_views.py` (replaced by §5).
- `validate_https` (only the dropped forms used it).
- The duplicate `validate_upload` and `_document_url`.

## 7. Verification

In this order, all green before the work is called done:

1. `uv run ruff check --fix . && uv run ruff format .`
2. `uv run djlint ohc_experience/templates --check` on touched templates.
3. `uv run python manage.py makemigrations --check` (expects no changes).
4. Full suite: `DATABASE_URL=postgres://localhost:5432/ohc_experience_dev
   USE_DOCKER=no uv run pytest -q -p no:sugar`. Baseline before this work:
   642 passed, 1 xfailed.
5. `grep` proves the boundary: no `abdm` import under `experiences/`, no
   `experiences.abdm` anywhere, and the only imports of `abdm` from
   organisations, support and pages are the product-aware ones listed in §8.

## 8. Left as is, on purpose

- `support`, `pages` and `organisations` import the portal's `Product`,
  `tracks`, session product key and organisation-verification services. The
  spec made tickets, routing and onboarding product-aware; that is a product
  decision, not misplaced code.
- The Care sandbox provisioning screens (`organisations.Sandbox`, the plugin
  client, the console's sandbox queue) are also dormant and unlinked from the
  nav, but they own a model and a migration. Dropping them is a data-affecting
  change and is not part of this split; flagged for a separate decision.
- The generic engine views, URL mounts (`/applications/`,
  `/ohc/applications/`) and templates stay: they are the abstraction's UI and
  render an empty list until a definition is registered.
