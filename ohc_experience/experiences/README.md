# Experience Engine

`experiences` is the Django app that owns the reusable workflow engine. It does
not import ABDM. Implementations register ordinary Python definitions through
`EXPERIENCE_IMPLEMENTATIONS`; `EXPERIENCE_PORTAL` selects the active portal.

## Boundary

- `models.py`: products, independent forms, immutable submission revisions,
  application/form links, dependencies, outcomes, workspaces, milestones,
  reviews, queries, append-only audit events, credentials, notifications and
  program-specific certification agency lists maintained by administrators.
- `definitions.py`, `registry.py`: implementation contracts and registration.
- `services.py`: record creation, scoped form reuse, submission/schema snapshots,
  attachment versioning and product-level outcome issuance.
- `workflows.py`: transactional submission/review state changes, queries,
  assignment, prerequisite enforcement and milestone materialization.
- `permissions.py`: organisation scope, staff area/category grants, filtered
  querysets, available review actions and assignment/decision access.
- `credentials.py`: encryption, audited reveal, rotation, revocation and callback
  validation. A registered provider supplies eligibility and gateway operations.
- `forms.py`, `fields.py`, `uploads.py`: shared form rendering and upload handling.
- `views.py`, `urls.py`, `admin.py`, `tasks.py`: HTTP, admin and background work.
  HTMX templates live in `templates/experiences`. Existing HTTP routes are kept.

Accounts, organisation membership, support tickets and events retain their
existing apps. Neither they nor the engine import a concrete implementation.

`CertificationAgency` is shared reference data keyed by program. Superusers manage
its name, active status and ordering in Django admin. Implementations query their
own program's active entries; submitted answers remain immutable name snapshots.

## Implementing a Program

Implementations contain no models, migrations, URL configuration or views:

1. Subclass `ReviewForm` with the domain's fields and validation. Define `sections`,
   `section_notes`, `section_badges` and `full_width_fields` for presentation.
2. Subclass `ApplicationFormDefinition` with a stable key, `form_class`,
   `reuse_scope` and schema version. Declare `allow_reuse` and
   `allow_approved_updates`; implement submission checks and lifecycle hooks.
3. Subclass `ApplicationDefinition` with its form definitions. `on_start` can
   return `OutcomeDefinition` values immediately when an application is created.
4. Subclass `ProgramDefinition` with the organisation form, product and milestone
   application types, milestone/track catalog, product-field mapping and branding.
   Catalog dependencies are validated and materialized in topological order.
   Optional `supplementary_applications` register independent review flows;
   `certification_application` selects the product's renewable certification flow.
5. Register the dotted program class in settings and select its key as the portal.
   Optionally supply a `CredentialDefinition` provider and demo builder.

Form hooks run inside the engine transaction: `initial_data` supplies defaults;
`submission_block_reason` gates final submission; `on_submit` projects validated
answers; `on_approve` returns structured outcomes; `on_send_back` updates domain
state. `snapshot_valid_until` stores the submitted evidence's expiry, while
`approval_block_reason` rechecks validity immediately before approval.
External provider calls cannot roll back with the database, so integrations
must implement idempotency and reconciliation.

The current product/milestone portal reviews one form per review item. The record
layer supports multiple forms per application; the portal does not implement an
arbitrary runtime-configurable workflow designer.

## History and Access

`FormRecord` has organisation, product or application reuse scope. Each
`ApplicationFormUse` pins an exact `FormSubmission`; edits never silently replace
another application's evidence. Revisions preserve answers, schema/version,
occurrence, revision and attachments. History renders the saved schema even when
the Python form changes. Downloads are permission-checked and served privately
from MinIO locally or S3 in production.

Organisation memberships govern integrator access. Assigned reviewers must also
hold the matching area/category capability; superusers have full access.
Team invitation and role constraints live in the
organisations app. Credentials are encrypted in `ProductCredential`, never stored
as secrets in outcome JSON. `ApplicationDependency` rejects cross-product links,
self references and cycles; the engine enforces prerequisite success statuses.

### Staff Permissions

`AccessGrant` grants a user access to a registered program, an area (`review`,
`support`, `events`), and a category from that program's track catalog. `""` is
General/onboarding; `"*"` explicitly includes every category. Grants are additive.
General review access covers organisation verification and product registration.
Category review access covers milestones selected under that track, including a
shared milestone selected under more than one track. It does not expose other
tracks or all revisions of a reused source form.
General review access also covers the program's product certification requests.
Renewals use a fresh application after each approval, preserving prior decisions
and outcomes. Approved attachments reused by a milestone are copied as file
references into its own submission, keeping its history and download permissions
independent of the source review.

| Area | Read | Write | Approve |
| --- | --- | --- | --- |
| Reviews | Queue, evidence, history, downloads | Raise and resolve queries | Approve or send back |
| Support | Tickets and attachments | Reply | Resolve |
| Events | Events | Create/edit drafts | Publish/unpublish |

Write and approve independently require read; neither implies the other.
Review mutations additionally require assignment (except for superusers).
`available_review_actions(user, item)` exposes actions for the current actor.
Use `visible_reviews`, `visible_submissions`, `visible_tickets`, and
`visible_events` for data reads, not the staff identity helper `reviewer()`.
Published event content is locked; an approver must unpublish it before editing.

Superadmins manage accounts and grants in the portal at `/portal/staff/` through
Staff & permissions. The directory supports search, pagination, active/archived
filters, account creation, profile/password changes and a category matrix for
each registered program and area. Only active superusers may access these routes
or call the management services. Applicant and superadmin accounts cannot be
edited or archived through this interface. Stale forms cannot overwrite a newer
account or permission change. Management changes are recorded in Django's
existing `LogEntry` audit store; password input is never logged.

New staff use `is_ohc_team=True`, `is_staff=False`, `is_superuser=False`. A
superadmin sets the initial password, and the provisioned work email is marked
verified. Duplicate emails, including another user's allauth addresses, are
rejected case-insensitively. Passwords use the configured Django validators.
Archiving sets `is_active=False`, deletes the account's database-backed sessions,
and releases pending review and support assignments. Evidence, decisions, audit
history and grants remain intact. Restore reactivates the saved grants but cannot
revive deleted sessions. Password/email changes also end existing sessions.

Staff can manage events at `/portal/events/manage/` with their existing scoped
event permissions; `is_staff` is not required. Portal navigation no longer links
to Django admin. The technical admin endpoint still exists separately for
maintenance, with its existing authorization checks. Staff identity and ordinary
Django model permissions alone grant no portal access.
Applicant organisation roles and invitation limits are unchanged. Applicants
cannot create staff grants. All new staff accounts default to no access.

Migration 0006 adds grants without deleting records or granting blanket access to
existing staff. Administrators must explicitly provision existing staff grants.
The DEBUG-only demo seeder provisions its named demo accounts explicitly.

## Existing Databases and Tests

Migrations 0003-0005 adopt the former `sandbox` tables into `experiences`, rename
domain-neutral fields and move content types, preserving row identities, foreign
keys, admin permission grants and scheduled tasks. They also support fresh
databases. The table-adoption migration is deliberately irreversible; restore a
backup when rolling back to the old code. Do not use demo reset for an upgrade.

`tests/example_program.py` is an unrelated supplier-quality implementation.
`tests/test_programs.py` exercises its full lifecycle, outcomes, reuse, queries,
dependencies and portal, and starts Django with ABDM imports blocked.
ABDM-specific regressions live in `abdm/tests`.

See [the ABDM guide](../../docs/abdm_sandbox.md) for local setup and
`seed_experience_demo`. The optional `--reset` flag is destructive.
