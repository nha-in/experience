# ABDM Developer Sandbox Portal — Design Document

Version 3 · 8 September 2026 · Prototype: `ABDM Sandbox Portal v3.dc.html` (Claude Design project
`c6703d59-d076-457c-b7c9-b18570b25705`; not importable from this session — the doc below is the
source of truth).

## 1. Purpose

A portal where integrators onboard to the ABDM sandbox, register a product, test it against the
sandbox gateway and request sandbox exit per compliance milestone; and where NHA reviewers verify
organisations, register products and decide on exit requests, with queries and send-backs recorded
against each form.

Two personas, one system of record:

- **Integrator** — signs up, describes the organisation, registers a product, files one exit request
  per milestone.
- **NHA reviewer** — works a queue of organisation verifications, product registrations and exit
  requests. Every decision is approve / send back / raise query, recorded with who and when.

## 2. Scope

In scope: onboarding (account, organisation, product), product overview, sandbox credentials
(username, password, callback URL, bridge URL), compliance tracks with per-milestone exit requests,
reviewer dashboard, review queue, review detail with decisions and queries, events, support tickets.

Out of scope for this release: automated conformance testing, AI assistance, agent-skill library,
public directory. Milestone content itself (what M1 requires) is NHA documentation, linked from the
portal.

## 3. Domain model

### Entities

**User** — name, email, password (hashed), role (`integrator` | `reviewer` | `admin`), organisation
(integrators only).

**Organisation** — name, description, type of entity (enum: private company, government body, sole
proprietorship, trust or society, section 8 company, LLP), category (enum: entity in India, foreign
entity with Indian subsidiary, academic or research institution), website (URL), logo (file),
registered address, pincode, state, district, verification document type (`PAN` | `GSTIN` | `CIN`),
verification document number, supporting document (file), verification status (`pending` |
`verified` | `sent_back`), verified_on, verified_by.

**Product** — organisation, name, description, category (enum: HMIS, LMIS, PHR application, health
locker, payer or TPA system, other), solution type (enum: Clinical HMIS, EUA, Health Locker),
applied milestones (set of Track × Milestone), sandbox id (`SBX-YYYY-NNNNN`), registration status
(`pending` | `registered` | `sent_back`).

**Credentials** — product, username (client id), password (client secret, shown once/revealable to
integrator, rotatable), gateway base URL, callback URL, bridge URL, issued_on, rotation_due,
callback last check (status code, latency, checked_at). Issued only once the organisation is
verified.

**Track** — code and name. Fixed set: `HI-CM`, `UHI`, `NHCX`, `PHR`, `HealthLocker`.

**Milestone** — code, name, track, order. Published by NHA per track:

| Track | Milestones |
|---|---|
| HI-CM | M1 ABHA and identity → M2 HIP services → M3 HIU services → M4 HFR registration |
| PHR | M1 ABHA and identity (this *is* HI-CM M1) → PHR1 PHR application flows |
| HealthLocker | PHR1 Locker flows |
| UHI | UHI1 UHI participant flows |
| NHCX | none published yet |

Milestones on a track open in order; a milestone is locked until the previous one is approved.

**Compliance (exit request)** — product, track, milestone, status, form fields, files, approval
fields:

- Form: start date, end date (sandbox testing window), tentative demo date, WASA audit agency name,
  WASA date.
- Files: functional testing certificate (PDF), functional testing report (PDF).
- Approval fields: approved_on, approved_by, decision note.
- Send-back fields: sent_back_on, sent_back_by, reason.
- Submitted_on, resubmission count.

All form inputs are required before the integrator can request exit.

**ReviewItem** — the reviewer-side wrapper for anything needing a decision. `type` (`exit_request`
| `organisation_verification` | `product_registration`), subject (FK to the compliance record,
organisation or product), submitted_on, assignee, status, decision, queries, history.

**Query** — review item, field key (or `form` for the whole form), question, raised_by, raised_at,
status, reply, replied_at, resolved_at.

### Status machines

Compliance record (integrator view):

```
locked ──(previous milestone approved)──▶ open
open / in_progress ──(request for exit, all fields + files present)──▶ under_review
under_review ──(reviewer raises query)──▶ query_raised ──(integrator replies)──▶ under_review
under_review ──(reviewer sends back)──▶ in_progress   (form unlocked, reason shown, resubmission allowed)
under_review ──(reviewer approves)──▶ approved        (approved_on / approved_by recorded; next milestone unlocks)
under_review ──(integrator withdraws)──▶ in_progress
```

Review item (reviewer view): `new → in_review → (query_raised ⇄ in_review) → approved | sent_back`.
A sent-back item returns to `in_review` when resubmitted, keeping its history. Organisation
verification and product registration follow the same machine; approval sets `verified` /
`registered` on the subject.

Query: `open → answered → resolved`. An item with any open query pauses; the reviewer marks answered
queries resolved.

### Rules

- One organisation per account; many products per organisation.
- Credentials are issued on organisation verification and are sandbox-only. Production access is
  granted per approved milestone (outside this portal's scope but recorded in the decision note).
- Approved milestones cannot be removed from a product. Adding a milestone after registration
  creates a new compliance record and notifies NHA.
- Every decision, query, reply, send-back and resubmission is an immutable history entry on the
  item and visible to both personas.

## 4. Information architecture

Integrator (sidebar): Overview · Credentials · Tracks: HI-CM, UHI, NHCX, PHR, HealthLocker · Events
· Support · Edit product.

Reviewer (sidebar): Dashboard · Review queue (→ Review detail).

Onboarding runs outside the shell as three steps: Account → Organisation → Product.

Routes:

```
/signup                                  /onboarding/organisation      /onboarding/product
/products/:sbxId                         /products/:sbxId/credentials  /products/:sbxId/edit
/products/:sbxId/tracks/:track           (milestone selected in-page)
/events                                  /support/:ticketId
/assess/dashboard                        /assess/queue                 /assess/review/:reviewId
```

## 5. Screens

### 5.1 Sign up
Your name · Name of the entity · Type of organisation (Company | Government | Sole proprietor) ·
Work email · Password (≥ 12 chars) · Captcha. Creates user + organisation shell; sends verification
email.

### 5.2 Organisation details
Header shows verification status badge (pending). Sections: Identity (name, description, type of
entity, category, website, logo), Registered address (address, pincode, state, district),
Verification document (type PAN/GSTIN/CIN, number, upload). Submit for verification → creates an
`organisation_verification` review item. Integrator may continue to register a product while
pending.

### 5.3 Product (register / edit)
Name · Description · Category · Solution type applying for · Tracks and milestones (checkbox per
milestone, grouped by track; NHCX shown disabled with "no milestones published"). Submit → creates
the product, one compliance record per selected milestone (first milestone `open`, rest `locked`),
and a `product_registration` review item. Edit mode reuses the form; approved milestones are locked.

### 5.4 Overview
Product header (name, SBX id, solution type, organisation, registered date), organisation
verification badge, Edit product. Tracks card listing applied tracks with a status pill per
milestone (click → track). Recent activity (history feed). Organisation card, Credentials summary
(client id, callback reachability, bridge URL), Upcoming events.

### 5.5 Credentials
Credentials card: username, password (masked, reveal), gateway URL, issued/rotation dates; Request
rotation, Revoke. Callback and bridge card: editable callback URL with last reachability check
(status, latency, time), editable bridge URL, Test callback, Save URLs. Warning: synthetic data
only.

### 5.6 Track
Header: track code, name, description, "n of m milestones approved". Milestone tiles (code, name,
status) — selecting a tile shows its exit request below. Detail card states:

- **Editable** (open / in progress): Sandbox testing (start, end, tentative demo date) · WASA
  (agency name, date) · Functional testing (certificate, report uploads) · Save draft · Request for
  exit (disabled until complete, with reason text). If previously sent back, an orange banner shows
  sent-back date, reviewer and reason.
- **Under review**: blue banner with submitted date and expected decision window; read-only summary;
  Withdraw request.
- **Query raised**: amber banner; read-only summary; Reviewer queries list with reply composer per
  open query; answered queries show the reply.
- **Approved**: green record — Approved on, Approved by, Decision, note; read-only summary with
  downloadable files.
- **Locked**: "Mx unlocks once My is approved."

Track not applied: empty state with Edit product. Track without published milestones (NHCX):
explanatory empty state.

### 5.7 Events, 5.8 Support
Filterable event list with registration states and past materials; ticket thread with severity,
SLA, product and track context, attachments, related knowledge.

### 5.9 Reviewer dashboard
Stat cards: In queue (breakdown by status), Median time to decision, Queries awaiting integrator,
Approved this month (by milestone). Needs a decision (five oldest, >10 days flagged). Decisions over
the last 8 weeks (approved vs sent back). Queue by type, exit requests by track, ageing buckets,
reviewer load including unassigned.

### 5.10 Review queue
Filter chips (All, Exit requests, Organisations, Products, Mine). Table: Type · Subject
(product/org + organisation) · Track · Submitted · Age · Assignee · Status. Sorted by age;
paginated. Row → review detail.

### 5.11 Review detail
Header: back to queue, review id, type tag, title, subject, submitted, age, assignee, status badge.

Left: **Submitted form** — sections and fields exactly as submitted, files openable, a **Query**
action on every field and **Query the whole form** on the card; fields with an open query carry a
"Query open" tag. **Integrator context** — organisation verification, prior approvals, callback
health, open tickets.

Right: **Decision** panel with a three-way switch:

- Approve — Approved on (default today), Approved by (default current reviewer), Decision note →
  Record approval.
- Send back — Reason (required) → Send back to integrator. Unlocks the integrator's form; item
  re-enters the queue on resubmission.
- Raise query — Against (field chosen via a field's Query action, or whole form), Question
  (required) → Send query. Pauses the item until answered.

Once decided, the panel is replaced by a **Decision recorded** card (title, on, by, note).
**Queries** card lists every query with status, the integrator's reply, and Mark resolved.
**History** lists all events newest first.

## 6. Permissions

| Action | Integrator | Reviewer | Admin |
|---|---|---|---|
| Edit own organisation / product | ✓ | – | – |
| Request exit, withdraw, reply to query | ✓ | – | – |
| View any organisation / product / exit request | – | ✓ | ✓ |
| Approve, send back, raise query, resolve | – | ✓ (assigned or any) | ✓ |
| Assign reviewers, publish milestones, events | – | – | ✓ |

Integrators see only their organisation's data. Reviewers never see the client secret.

## 7. Notifications

Email on: organisation verified / sent back; product registered; exit request received (to NHA),
query raised (to integrator), query answered (to reviewer), sent back, approved; callback check
failing three times in a row; event registration and reminders; ticket replies.

## 8. Non-functional

- Files: PDF only for certificates/reports (≤ 10 MB), images for logos; object storage with signed
  URLs; never expose file paths.
- Secrets: client secret stored encrypted; reveal endpoint is audited and rate-limited.
- Audit: every write on organisation, product, compliance and review items is logged with actor,
  timestamp and diff.
- Callback monitor: scheduled reachability check every 15 minutes per product; results stored for
  the credentials screen and overview alerts.
- Dates in IST; the sandbox holds synthetic data only (stated in UI).
- Accessibility: all controls keyboard-reachable; status never conveyed by colour alone (label +
  dot).

## 10. Open questions — answered

1. M4 on HI-CM → **HFR Registration**.
2. PHR M1 → **is HI-CM M1 itself** (one compliance record, shown on both tracks).
3. Reviewer assignment → **manual by admin**.
4. Decision SLA → **not required** (no SLA clock; ageing is still shown).
5. Production credential issuance → **handled outside the portal; the approval email tells the
   integrator that production onboarding follows separately**.
6. Editing while a query is open → **only after withdrawing** (reply or withdraw).
7. Support ticket ownership → **keep simple** (one NHA desk, existing ticket queue).

## 11. Decisions made while mapping the doc onto this repository

- **Reuse**: the vendor shell (`layouts/app.html`), console shell (`layouts/ohc.html`), careui
  components, `components/*` (form fields, file upload, secret value, copy button, messages), the
  organisations app (Organisation/Membership/Invitation, `OrganisationMixin`), users app
  (`is_ohc_team` = reviewer, `is_superuser` = admin), events and support apps, allauth signup,
  Celery, S3/MinIO storage, the test fixtures and factories.
- **New app** `ohc_experience.abdm` holds Product, Credential, ComplianceRecord, ReviewItem,
  ReviewQuery, ReviewHistory and AuditLog, with a code-defined track/milestone registry, a
  services module as the only write boundary, integrator views under `/products/` and
  `/onboarding/`, and reviewer views under `/assess/`.
- The generic `experiences` engine stays installed and routable but leaves the sidebar: the
  portal's IA is product-centric. The Care sandbox provisioning screens likewise stay routable
  but are no longer in either nav.
- Organisation verification moves from the console's status select onto the review item; the
  console's organisation page links to it.
- The vendor "dashboard" becomes a redirect to the current product's overview.
- Captcha: a dependency-free arithmetic challenge held in the session.
- Client secrets are encrypted at rest with Fernet (key derived from `SECRET_KEY`); reveal is
  audited and rate-limited through the cache.

## 12. Prototype reconciliation

The mockups (`ABDM Sandbox Portal v3.dc.html`) arrived after the first build
and the screens were re-drawn to them. Where the prototype and this document
disagree, the document's answers in §10 win: M4 is HFR registration, there is
no decision-SLA promise on the review banner, and the NHCX empty state links to
Events rather than to one hackathon. The prototype's brand (the care logo with
an "ABDM developer sandbox" caption), its 24-hour clock, its three ageing
buckets (0-3, 4-10, over 10 days) and its segmented decision switch were
adopted as drawn.
