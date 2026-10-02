# Collapsing the request chain

Plan, 2026-10-02. Drawn from `testing_new` at `4cf3dbf` plus the unstaged
track-filter and queue-chip changes of 2026-10-01. Prod is empty; the legacy
import has not run. That is the window for this.

## Why

One milestone request is stored as a 1:1:1 chain, `Milestone →
ApplicationInstance → ReviewItem`, plus an `ApplicationFormUse` row (and the
`forms` M2M it implements) and a `ProductWorkspace` beside the product. The
chain was built for multi-form, multi-step applications. Nothing uses either:
every application has one form use (20 of 20 in the demo), one review, one
milestone.

What the chain costs, measured on the demo database:

- Status lives on both `ApplicationInstance` and `ReviewItem`, in two
  vocabularies (`under_review` vs `in_review`/`new`), copied across by
  `_set_application_status` on every decision.
- The pinned submission lives on both `ReviewItem.selected_submission` and
  `ApplicationFormUse.selected_submission`; they disagree in 5 of 20 rows.
- `ReviewItem.organisation` and `.product` repeat the application's.
- `ApplicationInstance.metadata["milestone"]` repeats `Milestone.key`;
  `application_type` is `program.application_for(key)`, a catalog lookup.
- A request's track is nowhere. It is recomputed from
  `ProductWorkspace.applied_milestones` (JSON `"TRACK:key"` strings, read in
  11 files) joined to the milestone key. That is how the NHCX filter came to
  show P1 and an NHCX-only grant could approve it.
- Every "requests for this product" or "products that reached M1" query is a
  three-join with two status columns to choose between. The product review
  page and the coming product list view are both exactly those queries.

The track question is already settled: `TrackDefinition.keys` are disjoint,
`milestone_keys()` only accepts `track.code:key` for the track's own keys, so
track is a function of the key. No track column; the filter is "key in
`track.keys`". M1 is shown on the UHI page only as a prerequisite.

## Target shape

```
Product
  reference, experience_type, solution_type, registered_at   ← from ProductWorkspace
  (applied_milestones is derived: enabled milestone requests)

Request                                                       ← today's ReviewItem
  kind            organisation | product | milestone | certification
  organisation, product (null for organisation)
  milestone_key   null unless kind=milestone; definition and track via catalog
  enabled         from Milestone; only kind=milestone
  reference       stored, from ApplicationInstance (EXIT-…, UHI-…, WASA-…)
  created_by      from ApplicationInstance
  form, selected_submission                                   one pin
  status          one vocabulary (ReviewItem.Status)
  submitted_at, resubmission_count, assignee
  decided_at, decided_by, decision_reason, decision_note      the final say
  created_at

RequestDependency(request, depends_on)                        ← ApplicationDependency
FormSubmission.origin_request                                 ← origin_application
ProductOutcome.source_request                                 ← source_application
ReviewQuery.item → request                                    unchanged
AuditEvent.item → request                                     unchanged
```

Dropped: `ProductWorkspace`, `Milestone`, `ApplicationInstance`,
`ApplicationFormUse`, `ApplicationInstance.forms`, `ApplicationInstance.dependencies`.

Kept as they are: `FormRecord`/`FormSubmission` (reuse is real: each
product-scoped evidence form serves four requests), `FormAttachment`,
`ProductOutcome` (credentials, WASA and milestone approvals all issue through
it), `ProductCredential`, `AuditEvent`, `ReviewQuery`, `AccessGrant`,
`CertificationAgency`.

`application_type` becomes a property: `Request.definition` is
`program.application_for(milestone_key)` for milestones, the certification
definition for WASA, the organisation/product forms otherwise. The
`ApplicationDefinition` classes (`on_start`, `on_approve`, `success_statuses`,
`auto_approve`, `reference_prefix`) stay; only what row they attach to changes.

### Phase 2, recorded so it is not rediscovered

Several reviewer passes per request are wanted later (legacy's
`htc1_status`…`htc4_status` plus `admin_status`). That is a one-to-many
*under* Request, not a layer above it:

```
Review(request, submission, reviewer, verdict, note, created_at)
```

`ReviewQuery` moves to hang off a Review. Request keeps the final decision
fields. The migration that creates Review backfills one row per decided
Request from `AuditEvent` ("Approved"/"Rejected" rows carry actor, time and
submission id). Nothing in this plan pre-builds for it; the only concession is
keeping the decision fields together on Request so they lift cleanly.

## Order of work

Each step lands on its own, passes the full suite, and leaves the app
working. A step's migration is written with its test
(`tests/test_*_migration.py`, `SET CONSTRAINTS ALL IMMEDIATE`, never
`transaction=True`). Reference counts are from `testing_new` excluding tests
and migrations.

### 1. Fold `ProductWorkspace` into `Product`

7 files, 14 class refs, 44 `.workspace` attribute reads (111 of
`workspace.reference`, 101 of `workspace.product` once tests are counted).

- Add `reference`, `experience_type`, `solution_type`, `applied_milestones`,
  `registered_at` to `Product`; copy; drop the table.
- `Product.definition`, `needs_callback`, `callback_codes`,
  `get_solution_type_display`, `get_absolute_url` move across.
- `applied_milestones` stays a JSON column for this step. It becomes derived in
  step 4.
- Importer (`legacy_import/writer.py`) writes `Product` fields instead.

Standalone value: one object per product; every `product.workspace.x` read
becomes `product.x`.

### 2. Fold `Milestone` into `ReviewItem`

14 files, 30 refs; 26 `.milestone` attribute reads.

- Add `milestone_key` and `enabled` to `ReviewItem`; copy from `Milestone`
  via the 1:1 on `application`; drop `Milestone`.
- `ReviewItem.milestone_definition` (`program.milestones[key]`) and
  `track_codes` replace `Milestone.definition` and `.track_codes`.
- `UniqueConstraint(product, milestone_key)` where `kind=application and
  milestone_key != ""`.
- `permissions.track_items` becomes `Q(milestone_key__in=track.keys,
  enabled=True)`. The `applied_milestones__contains` lookups go.
- `review_order`, `queue_presentation`, `waiting_rows`, the track page, the
  dashboard counts and `project_product` read the key from the review.

Standalone value: the queue, grants and dashboard stop depending on the JSON
list. Fixes the class of bug behind P1-under-NHCX for good.

### 3. Collapse `ApplicationInstance` and `ApplicationFormUse` into `ReviewItem`

4 + 4 files by class name, but 122 `.application` attribute reads: this is the
big one, and it is mostly mechanical substitution.

- Add `reference`, `created_by` to `ReviewItem`; copy from the application.
  Organisation and product-registration reviews get references minted the
  same way (`ORG-…`, `REG-…`) so every request has one.
- `ApplicationDependency` → `RequestDependency(request, depends_on)`;
  `ReviewItem.dependencies` M2M through it. `_prerequisite_applications`,
  `pending_dependants`, `waiting_reviews` walk reviews.
- `FormSubmission.origin_application` → `origin_review` (FK to ReviewItem);
  `ProductOutcome.source_application` → `source_review`. 24 readers outside
  models.
- `ReviewItem.definition` returns the `ApplicationDefinition` (via
  `application_for(milestone_key)`, or certification, or the kind's form);
  the current `definition` (the form definition) is renamed
  `form_definition` first, in its own commit, so the switch is greppable.
- `create_application` + `materialize_application_forms` become
  `create_request`: mint reference, create the review with its form record and
  pin, run `on_start`.
- `_set_application_status` is deleted. `success_statuses` is compared against
  `ReviewItem.status`; the only non-trivial case is `uhi1` (`auto_approve`),
  which already lands on `approved`.
- Drop `ApplicationFormUse`, `ApplicationInstance.forms`, `ApplicationInstance`.
- Unique constraint on `reference`.
- Importer writes one row where it wrote three.

Standalone value: one status, one pin, one reference per request.

### 4. Rename `ReviewItem` → `Request`; derive `applied_milestones`

- Model rename (`RenameModel`, keep the table name if the churn is not worth
  it; the Python name is what matters). `related_name`s: `review_items` →
  `requests`, `review_item` → `request`.
- `Product.applied_milestones` becomes a property over enabled milestone
  requests. The registration form keeps posting `"TRACK:key"` selections;
  `project_product` turns them into `enabled` flags, which it already does.
  Drop the column.
- `Request.Kind` gains `CERTIFICATION` so `application_type ==
  certification.key` checks become `kind == CERTIFICATION`.

Standalone value: the name says what it is; the last JSON-list reader goes.

### 5. Product list view

First consumer of the new shape; written against it, not before it.

- `/assess/products/`: products visible to the reviewer, filters for track,
  milestone reached (`requests__milestone_key=…, requests__status=approved`),
  milestone pending, organisation, solution type, provisioning state.
- Shares `visible_products` with the queue. Dashboard counts link into it and
  into the queue with filters set, replacing the current track rows.

## Not in scope

- Merging `ReviewItem` and `ReviewQuery`, or anything on `FormRecord` /
  `FormSubmission`. Reuse and revision history work and are used.
- Moving tracks or milestones into tables. NHA changes them by release.
- The phase-2 `Review` table.
- `legacy_import` beyond keeping it compiling at each step. It has not run; it
  gets written once against the final shape rather than migrated through four.

## Risks

- Step 3 touches every module that mentions `item.application`. Tests: 15
  files read `.application`, 10 read `.workspace`. Budget for the test churn
  being larger than the code churn.
- `ProductOutcome` unique constraint is `(product, outcome_type,
  source_application)`; it moves to `source_request` unchanged.
- The `0016` data migration and the seven migration tests reference
  `ApplicationInstance` through `apps.get_model`; they keep working since
  historical models are frozen, but any new migration test written during
  steps 1–3 must target the step's own state.
- `experience-legacy-import` worktree is on `aafdfbd`; its writer is the one
  place outside `experiences/` that creates these rows. Rebase it after step 4,
  not after each step.
