# ABDM Implementation

This is a regular Python package, not a Django app. It has no AppConfig, models,
migrations, views or URL configuration. All persistence and workflow execution
belong to `experiences`.

- `forms.py`: ABDM organisation, product and exit-evidence fields and validation.
- `catalog.py`: ABDM tracks, milestones and prerequisite definitions.
- `definitions.py`: form/application identities, ABDM eligibility and review hooks,
  product mappings, structured approval outcomes and portal labels.
- `gateway.py`: ABDM credential eligibility and provider/demo integration.
- `wasa.py`: approved product WASA selection, expiry checks and review outcomes.
- `demo.py`: the ABDM data builder invoked by `seed_experience_demo`.
- `tests/`: domain-specific regression coverage.

The dependency direction is `abdm -> experiences`. Settings select
`ohc_experience.abdm.definitions.ABDM`; the engine discovers it through its registry,
never by importing this package directly. Stable persisted form/application keys
are retained so existing submissions and outcomes remain accessible.

See [the engine contracts](../experiences/README.md) and
[the portal setup guide](../../docs/abdm_sandbox.md).

## Organisation location validation

`OrganisationForm` uses the shared organisation LGD lookup service to resolve a
six-digit PIN code into State and District. A single distinct LGD code pair fills
both fields; multiple pairs require selection. The form validates the location
against LGD on submission and persists canonical names plus `state_lgd_code` and
`district_lgd_code` in its existing JSON answers. No database migration is required.
Unknown PINs and unavailable lookups block saving an unverified location; the UI
keeps the PIN available for correction or retry.

Set the server-only `LGD_API_KEY` for registration, editing and local demo
seeding. `LGD_API_URL` defaults to
`https://apissbx.abdm.gov.in/global/api/v3/internal/lgd`; `LGD_API_TIMEOUT`
defaults to 5 seconds and `LGD_CACHE_TTL` to 3600 seconds. Automated tests use
controlled lookup fixtures. There is no fake PIN mapping for interactive use or
demo seeding, and no credential is sent to the browser.

See [Organisation address lookup](../../docs/abdm_sandbox.md#organisation-address-lookup)
for configuration bounds, the legacy source trace and the verified sandbox
response contract.

## UHI participation

Product registration selects the UHI track and milestones. Role, service and
optional detail questions belong to the separate UHI application, which opens
once M1 is submitted. UHI shares M1 with HIE-CM and PHR. The UHI application
records participation without a reviewer decision or exit-evidence uploads:
submitted after M1 and organisation verification are approved, it is recorded at
once; submitted earlier, it waits in the queue and is recorded the moment they
are. The application remains visible to reviewers, and WASA exit reviews retain
their normal approval process.

## WASA certification agency

The exit-evidence dropdown reads active ABDM rows from the engine's
`CertificationAgency` table each time a form is built. Superusers manage names,
active status and display order in Django admin under **Experiences →
Certification agencies** (`/admin/experiences/certificationagency/` with the
default admin URL). Changes take effect without a code deployment or restart.

Migrations 0007–0008 create the table and seed the 255 options from the supplied
legacy frontend's `src/constants/security-audit-agencies.js` once, preserving
their original order and exact values. Run `python manage.py migrate` before
serving the updated forms. The seed is the old portal's list; subsequent updates
are managed by administrators. There is no live external feed or static fallback.

New selections must be active entries in the ABDM list. Previously saved agency
names remain available on their existing forms, including after a rename or
deactivation, and submission history keeps its original names. The name remains
in the submission's JSON snapshot; changing the master table does not rewrite
historical evidence. Use deactivation to retire an agency; admin deletion is
disabled. Demo resets preserve this table and use an active agency from it.

## Product WASA lifecycle

WASA can be submitted with milestone exit evidence or independently from the
product's **WASA certification** section. Independent submissions use the
`abdm_wasa_review` application and the product-scoped `abdm_wasa` form. Each
renewal creates a separate application and review item while retaining the
existing form submission history. Drafts, sent-back submissions and pending
renewals do not replace the approved product certificate.

Approving a new certificate issues a `ProductOutcome` of type `wasa_approval`.
Its data pins the reviewed `submission_id`, agency, audit date and explicit
`wasa_valid_until`; its `valid_until` also stores that expiry date. No validity
period is inferred. `current_wasa(product)` selects the latest audit date, then
approval time and outcome ID, so approving older evidence later cannot replace
a newer certificate. A revoked current certificate remains visible as revoked;
it does not silently fall back to an earlier certificate. Expiry is evaluated
against the portal's local date, including at the final approval decision.

New exit-evidence milestones default to the approved, unexpired product
certificate when one is available. Their snapshots retain its
`wasa_source_submission` and a copy of
the certificate attachment. The server resolves that reference through approved
outcomes belonging to the same product and copies the approved audit details;
posted replacement details cannot alter a reused certificate. A milestone that
has saved a source retains that exact pin after a subsequent renewal. Approving
a milestone that reuses WASA does not issue another WASA outcome. Clearing the
reuse choice allows a new certificate to be submitted for review.

Existing approved milestone evidence without a product WASA outcome is shown
as **Expiry needs verification** and cannot be reused as a valid product
certificate. Historical milestone evidence and approvals remain intact. This
feature uses existing application, submission, attachment and outcome tables;
it requires no database migration or historical date backfill.
