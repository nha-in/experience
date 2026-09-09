# Experiences / ABDM Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `experiences` a self-contained engine with no ABDM code and its own tests, make `abdm` the only home of ABDM code, drop the dormant production-access flow, and give shared plumbing (uploads, mail, the status-dot colour) one obvious home.

**Architecture:** Pure moves and deletions; no model changes, no migrations, no settings changes. The engine's form contract (`ExperienceForm`, upload validation, the file-widget shape) moves into the engine. Mail delivery moves to a new plain package `core`. The engine's tests run against a small `SampleExperience` registered only from the test conftest, so the shipped registry is empty until a real app registers a definition.

**Tech Stack:** Django 6, django-htmx, pytest + factory-boy, PostgreSQL, ruff, djlint.

**Spec:** `docs/superpowers/specs/2026-09-09-experiences-abdm-split-design.md`

## Global Constraints

- No git commits in this session (the user has not asked for them).
- Every task ends with the full suite green: `DATABASE_URL=postgres://localhost:5432/ohc_experience_dev USE_DOCKER=no uv run pytest -q -p no:sugar`. Baseline: 642 passed, 1 xfailed.
- No model or migration changes: `uv run python manage.py makemigrations --check` stays clean.
- Lint gates before the final run: `uv run ruff check --fix . && uv run ruff format .`; `uv run djlint <touched templates> --check`.
- Templates are not redesigned; only `{% load %}` lines change.
- Ignore `.claude/worktrees/**` (other branches).

---

## File structure

**Created**

| File | Responsibility |
|---|---|
| `ohc_experience/core/__init__.py` | Plain package for cross-cutting plumbing. Not an app. |
| `ohc_experience/core/mail.py` | `deliver`, `absolute_url`, `team_recipients`. |
| `ohc_experience/experiences/uploads.py` | Upload limits, `validate_upload(s)`, `ExistingFile`, `existing_file`, `ModelFileFormMixin`. |
| `ohc_experience/experiences/tests/sample.py` | `SampleExperience`: forms, definitions, actions, roles; `register()`. |
| `ohc_experience/experiences/tests/conftest.py` | Registers the sample definition. |
| `ohc_experience/experiences/tests/test_uploads.py` | Upload helpers and the mixin. |
| `ohc_experience/experiences/tests/test_definitions.py` | Registry and definition contract, no DB. |
| `ohc_experience/experiences/tests/test_services.py` | Port of `test_engine.py` onto the sample. |
| `ohc_experience/experiences/tests/test_views.py` | Port of the view half of `test_seed_and_views.py` onto the sample. |

**Modified**

| File | Change |
|---|---|
| `experiences/forms.py` | + `ExperienceForm`. |
| `experiences/apps.py` | `ready()` removed. |
| `experiences/README.md` | Rewritten around the sample. |
| `abdm/uploads.py` | Only `document_url` and `PortalFileFormMixin` remain. |
| `abdm/forms.py`, `abdm/selectors.py` | Import from `experiences.uploads` / `.uploads`. |
| `abdm/notifications.py` | Uses `core.mail` and `organisations.selectors`. |
| `abdm/templatetags/abdm_extras.py` | `dot_class` removed. |
| `organisations/selectors.py` | + `notification_recipients`. |
| `organisations/forms.py` | Imports from `experiences.uploads` and `abdm.uploads`. |
| `support/forms.py`, `support/notifications.py`, `events/notifications.py` | New import homes. |
| `ohc_experience/conftest.py` | `mail_outage` patches `core.mail.send_mail`. |
| `theme/templatetags/careui.py` | + `dot_class`. |
| `templates/components/status_dot.html` | `{% load careui %}`. |

**Deleted**

- `experiences/abdm/` (definition, forms), `experiences/management/` (seed), `experiences/tests/test_engine.py`, `experiences/tests/test_seed_and_views.py`.

---

## Interfaces (shared by every task)

### `experiences/uploads.py`

```python
MB = 1024 * 1024
PDF = frozenset({".pdf"})
IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
DOCUMENTS = PDF | IMAGES
PDF_MAX_MB = 10
IMAGE_MAX_MB = 2

def validate_upload(upload, *, extensions, max_mb: int) -> None
    # None and False (a cleared FileField) pass. Wrong suffix or > max_mb MB raise ValidationError.
def validate_uploads(uploads, *, extensions, max_mb: int) -> None

@dataclass(frozen=True)
class ExistingFile:
    pk: str; original_name: str; size: int; download_url: str

def existing_file(instance, field_name: str, *, download_url: str) -> ExistingFile | None

class ModelFileFormMixin:
    def download_url(self, field_name: str) -> str: ...   # hook; default ""
    existing_files: dict[str, list[ExistingFile]]
    removed_file_ids: dict[str, set[str]]
    def new_upload(self, name) -> UploadedFile | None
    def has_file(self, name) -> bool
    def clean(self)   # a removal without a replacement sets cleaned[name] = False
```

### `experiences/forms.py`

```python
class ExperienceForm(forms.Form):
    def __init__(self, *args, experience_context=None, existing_files=None, **kwargs)
    def retained_existing_files(self, field_name) -> list
    def clean(self)                       # min/max files across retained + new for MultipleFileField
    def require_upload(self, field_name, label) -> None
```

### `abdm/uploads.py`

```python
def document_url(kind: str, pk: int, field: str) -> str      # reverse("products:document")
class PortalFileFormMixin(ModelFileFormMixin):
    download_kind = ""                                       # "organisation" | "compliance"
    def download_url(self, field_name) -> str
```

### `core/mail.py`

```python
def deliver(subject: str, body: str, recipients: list[str]) -> None   # logs, never raises
def absolute_url(path: str) -> str
def team_recipients() -> list[str]        # settings.ABDM_REVIEW_INBOX or every active OHC team email
```

### `organisations/selectors.py`

```python
def notification_recipients(organisation) -> list[str]   # owner/admin members + technical contact, sorted, deduplicated
```

### `abdm/notifications.py`

```python
def reviewer_recipients(item=None) -> list[str]   # item.assignee email, else team_recipients()
# notify_* unchanged
```

### `theme/templatetags/careui.py`

```python
@register.filter
def dot_class(variant) -> str    # success/primary → bg-emerald-500, info → bg-sky-500, warning → bg-amber-500, destructive → bg-red-500, else bg-neutral-300
```

### `experiences/tests/sample.py`

Key `sample_experience`, prefix `SMP`, statuses `draft, submitted, under_review, changes_requested, revision_submitted, approved, rejected, withdrawn`, permissions `COMMON_PERMISSIONS`, roles `applicant_owner, applicant_submitter, applicant_contributor, applicant_viewer, reviewer, decision_maker, review_observer` (same permission sets as the dropped ABDM definition).

Forms, in order, with the engine feature each exists for:

| key | name | class | feature |
|---|---|---|---|
| `profile` | Organisation profile | `ProfileForm(legal_name, contact_email)` | `allow_updates`, `metadata_updates` |
| `scope` | Integration scope | `ScopeForm(channels: web/mobile/locker)` | dependency on profile; `metadata_updates` |
| `locker_operations` | Locker operations | `LockerForm(locker_name)` | `is_applicable` only when `"locker"` in scope channels |
| `readiness` | Technical readiness | `ReadinessForm(endpoint_url, region)` | plain dependent form |
| `compliance` | Compliance evidence | `ComplianceForm(assessment_date, report: pdf)` | single file + form action `verify_evidence` |
| `certification` | Security certification | `CertificationForm(certificate_number, issued_on, expires_on, certificate_documents 1–5 pdf, supporting_documents ≤8 pdf/png/jpg)` | `repeatable`, `valid_until_field="expires_on"`, `renewal_window_days=45`, editable while approved |
| `declaration` | Declaration | `DeclarationForm(signatory_name, confirmed)` | `allow_updates=False` |

Actions: `submit`, `ask_review_team` (`ApplicantQueryForm`), `start_review`, `raise_query` (`RaiseQueryForm`), `approve` (`ApprovalForm(client_id, effective_date, note)` → outcome), `reject` (`RejectionForm(reason, details, note)`, destructive).

`register()` registers idempotently and returns the definition.

---

## Task 1: Engine contract into the engine

**Files:** create `experiences/uploads.py`, `experiences/tests/test_uploads.py`; modify `experiences/forms.py` (+ `ExperienceForm`), `experiences/abdm/forms.py` (import the moved pieces from the engine; keep `validate_https`).

- [ ] Write `test_uploads.py`: `validate_upload` passes `None` and `False`; rejects `report.exe` with "file types"; rejects a 3 MB upload at `max_mb=2` with "smaller than"; `validate_uploads` checks every item; `existing_file` returns `None` for an empty field and an `ExistingFile` whose `pk` is the field name otherwise; a `ModelFileFormMixin` ModelForm over `ApplicationAttachment.file` lists the saved file under `existing_files`, `has_file` is true, posting `remove_files__file=file` without a replacement makes `cleaned_data["file"] is False` and `has_file` false, and `download_url` is whatever the subclass returns.
- [ ] Create `experiences/uploads.py` from the abdm module: same constants and mixin, `validate_upload` accepting `None`/`False` and any iterable of extensions, `existing_file` taking `download_url=`, the mixin's `__init__` calling `self.download_url(name)`.
- [ ] Move `ExperienceForm` into `experiences/forms.py` (imports `MultipleFileField` from `.fields`, `ValidationError`, `_`).
- [ ] `experiences/abdm/forms.py`: replace its `ExperienceForm`, `validate_upload`, `validate_uploads` with imports from `ohc_experience.experiences.forms` / `.uploads`.
- [ ] Run the suite: green.

## Task 2: Sample definition and the engine's own tests

**Files:** create `experiences/tests/sample.py`, `experiences/tests/conftest.py`, `experiences/tests/test_definitions.py`, `experiences/tests/test_services.py`, `experiences/tests/test_views.py`.

- [ ] `sample.py` per the interface block. Forms subclass `ExperienceForm`; `ComplianceForm.clean_report` uses `validate_upload(..., extensions=PDF, max_mb=10)` and `clean` calls `require_upload("report", "assessment report")`; `CertificationForm` validates both groups with `validate_uploads`, refuses `expires_on <= issued_on`, and requires certificate documents. `RaiseQueryForm.__init__` fills `related_form` choices from the registry. `register()`:

```python
def register():
    if SampleExperience.key not in {item.key for item in registry.all()}:
        registry.register(SampleExperience)
    return SampleExperience
```

- [ ] `conftest.py`: `from ohc_experience.experiences.tests import sample` then `sample.register()` at import.
- [ ] `test_definitions.py` (no DB): a definition with a duplicate form key raises `ImproperlyConfigured`; a role naming an unknown permission raises; a form depending on an unknown key raises; the sample validates; `SampleExperience.forms` count is 7; `assignable` role audiences; `Profile.availability` reasons for wrong status and missing permission using a stub context (`ExperienceContext` with empty submissions).
- [ ] `test_services.py`: port every test in `test_engine.py` with this key map: `organisation_profile→profile`, `product_use_case→scope`, `integration_scope` data `{"abdm_roles": ["hip","health_locker"]}` → `scope` data `{"channels": ["web","locker"]}`, `health_locker_operations→locker_operations`, `technical_readiness→readiness`, `security_compliance→compliance` (`verify_evidence` message "Evidence verified"), `security_certification→certification`, `declaration→declaration`; `FORM_COUNT=7`, `BASE_REQUIRED_FORM_COUNT=6`, scoped progress `3/7 → 43`, base progress `2/6 → 33`; approve `cleaned_data={"client_id": "CLIENT-1001", "effective_date": ..., "note": ...}` → `outcome["client_id"]`.
- [ ] `test_views.py`: a `workspaces` fixture builds, through services, what the seed used to: an applicant (owner), a contributor (developer member), an admin (`is_ohc_team`, `is_staff`) with a `decision_maker` grant; a draft application with `profile`, `scope` (`web`+`locker`), `locker_operations` complete and one applicant query awaiting the reviewer; a review application with `scope` = `web` only, every applicable form complete, `certification` renewed once (submission 2, `expires_on` in 20 days, two files per group), submitted and under review. Port every non-seed test from `test_seed_and_views.py` onto it, plus one new test: with the registry emptied via `monkeypatch.setattr(registry, "_definitions", {})` the applicant list returns 200 without a Start button.
- [ ] Run the suite: old and new engine tests both green.

## Task 3: Drop the production-access flow

**Files:** delete `experiences/abdm/`, `experiences/management/`, `experiences/tests/test_engine.py`, `experiences/tests/test_seed_and_views.py`; modify `experiences/apps.py`, `experiences/README.md`.

- [ ] Delete the four paths. `apps.py` keeps only the `AppConfig` with `name` and `verbose_name`.
- [ ] README: main pieces (+ `uploads.py`, `forms.py::ExperienceForm`), "Add an experience" pointing at `tests/sample.py`, form-dependency example using `Compliance`/`Readiness`, HTMX section unchanged, demo-data section removed, a line that the registry ships empty.
- [ ] `grep -rn "experiences.abdm\|seed_experience_demo\|validate_https" ohc_experience config docs README.md` finds nothing outside the 8 September docs.
- [ ] Run the suite: green (engine covered by Task 2's tests).

## Task 4: Upload plumbing consumers

**Files:** modify `abdm/uploads.py`, `abdm/forms.py`, `abdm/selectors.py`, `organisations/forms.py`, `support/forms.py`.

- [ ] `abdm/uploads.py` → `document_url` + `PortalFileFormMixin` only (docstring says why it exists).
- [ ] `abdm/forms.py`: `PDF`, `PDF_MAX_MB`, `validate_upload` from `ohc_experience.experiences.uploads`; `ExitRequestForm(PortalFileFormMixin, forms.ModelForm)`.
- [ ] `abdm/selectors.py`: drop `_document_url`, use `document_url`.
- [ ] `organisations/forms.py`: limits and validator from `experiences.uploads`, mixin from `abdm.uploads`.
- [ ] `support/forms.py`: `DOCUMENTS`, `PDF_MAX_MB`, `validate_upload` from `experiences.uploads`.
- [ ] Run the suite: green. `grep -rn "abdm.uploads" ohc_experience` lists only `organisations/forms.py` and `abdm/forms.py`.

## Task 5: Mail plumbing

**Files:** create `core/__init__.py`, `core/mail.py`, `core/tests/__init__.py`, `core/tests/test_mail.py`, `organisations/tests/test_selectors.py`; modify `organisations/selectors.py`, `abdm/notifications.py`, `support/notifications.py`, `events/notifications.py`, `ohc_experience/conftest.py`.

- [ ] Tests first: `deliver` swallows an `OSError` from `send_mail` and logs it; `absolute_url` uses the Site domain with `https` when `DEBUG` is off and `http` when on; `team_recipients` returns `[settings.ABDM_REVIEW_INBOX]` when set, else active OHC team emails sorted, excluding inactive and blank; `notification_recipients` returns owner + admin + technical contact, not developers, deduplicated and sorted.
- [ ] Implement `core/mail.py` and `notification_recipients`.
- [ ] `abdm/notifications.py`: imports from `core.mail` and `organisations.selectors`; `reviewer_recipients(item)` keeps only the assignee branch then `team_recipients()`; `notify_*` call `notification_recipients` where they called `integrator_recipients`.
- [ ] `support/notifications.py`, `events/notifications.py`: new imports.
- [ ] `conftest.py`: patch `ohc_experience.core.mail.send_mail`.
- [ ] Run the suite: green. `grep -rn "abdm.notifications" ohc_experience` lists only `abdm/` and the conftest docstring, if any.

## Task 6: Status-dot filter

**Files:** modify `theme/templatetags/careui.py`, `abdm/templatetags/abdm_extras.py`, `templates/components/status_dot.html`.

- [ ] Add `dot_class` to careui with the same mapping; remove it from `abdm_extras`; `status_dot.html` loads `careui`.
- [ ] `grep -rn "dot_class" ohc_experience theme` shows only careui and `status_dot.html`.
- [ ] Run the suite: green (many ABDM view tests render the dot).

## Task 7: Docs and final gates

- [ ] `abdm/__init__.py` docstring: "The ABDM sandbox portal: products, credentials, compliance tracks, the review queue." Root README unchanged unless it mentions removed commands.
- [ ] `uv run ruff check --fix . && uv run ruff format .`
- [ ] `uv run djlint ohc_experience/templates/components/status_dot.html --check`
- [ ] `uv run python manage.py makemigrations --check`
- [ ] Full suite green; boundary greps from the spec §7.5 pass.
- [ ] Start the dev server and load `/`, `/applications/`, `/assess/dashboard/` and a product overview as the demo users to confirm nothing 500s after the template-tag move.

---

## Self-review

- Spec coverage: §4.1 → Tasks 1–3; §4.2–4.5 → Tasks 4–5; §4.6 → Task 6; §4.7 → Task 5; §5 → Task 2; §6 → Task 3; §7 → Task 7; §8 is untouched by design.
- Names used across tasks match the interface block.
