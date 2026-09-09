# ABDM Sandbox Portal data model

Entity-relationship reference for the OHC Experience portal, read from Django's model registry on 2026-09-09: 23 tables across six apps. Boxes without columns are entities detailed in another section. Solid lines are required foreign keys; dashed lines are nullable ones (SET_NULL). Columns commented `User` point at `users_user`.

## Overview

```mermaid
erDiagram
  Organisation ||--o{ Membership : "has members"
  User ||--o{ Membership : "belongs through"
  Organisation ||--o{ Invitation : "issues"
  Organisation ||--o| Sandbox : "is provisioned"
  Organisation ||--o{ Product : "registers"
  Product ||--o| Credential : "holds"
  Product ||--o{ ComplianceRecord : "one per track and milestone"
  Organisation ||--o{ ReviewItem : "is the subject of"
  Product |o..o{ ReviewItem : "registration, exit"
  ComplianceRecord |o..o| ReviewItem : "exit request"
  ReviewItem ||--o{ ReviewQuery : "raises"
  ReviewItem ||--o{ ReviewHistory : "records"
  Organisation ||--o{ ApplicationInstance : "applies, PROTECT"
  ApplicationInstance ||--o{ ApplicationFormSubmission : "form revisions"
  ApplicationFormSubmission ||--o{ ApplicationAttachment : "files"
  ApplicationInstance ||--o{ ApplicationAccess : "grants"
  User ||--o{ ApplicationAccess : "holds"
  ApplicationInstance ||--o{ ApplicationQueryThread : "queries"
  ApplicationFormSubmission |o..o{ ApplicationQueryThread : "about"
  ApplicationQueryThread ||--o{ ApplicationQueryMessage : "messages"
  ApplicationInstance ||--o{ ApplicationEvent : "audit trail"
  ApplicationFormSubmission |o..o{ ApplicationEvent : "about"
  Organisation ||--o{ Ticket : "opens"
  Product |o..o{ Ticket : "concerns"
  Ticket ||--o{ TicketMessage : "thread"
  Event ||--o{ EventRegistration : "sign-ups"
  User ||--o{ EventRegistration : "registers"
  User |o..o{ AuditLog : "acted"
```

## Identity and organisations

_Apps: users · organisations · 5 tables_

Every integrator signs in as a User and acts through a Membership in one Organisation. The organisation is the hub of the whole model: products, tickets, applications and review items all hang off it. OHC staff are ordinary users with the is_ohc_team flag and usually no membership at all.

```mermaid
erDiagram
  Organisation ||--o{ Membership : "has members"
  User ||--o{ Membership : "belongs through"

  User {
    bigint id PK
    varchar email UK
    varchar name
    varchar phone_number
    bool is_ohc_team "works the review desk"
    bool is_staff "Django admin access"
    bool is_superuser
    bool is_active
    varchar password "argon2 hash"
    timestamptz last_login
    timestamptz date_joined
  }
  Organisation {
    bigint id PK
    varchar name
    varchar slug UK
    varchar legal_name
    text description
    varchar entity_type "6 types, see vocabulary"
    varchar category "3 categories, see vocabulary"
    varchar logo "file"
    varchar website
    text registered_address
    varchar pincode
    varchar district
    varchar city
    varchar state
    text deployment_regions
    varchar technical_contact_name
    varchar technical_contact_email
    varchar technical_contact_phone
    varchar verification_document_type "PAN | GSTIN | CIN"
    varchar verification_document_number
    varchar verification_document "file"
    timestamptz verification_submitted_at
    varchar verification_status "pending | verified | sent_back"
    text verification_reason
    bigint verified_by_id FK "User"
    timestamptz verified_at
    timestamptz onboarded_at
    timestamptz created_at
    timestamptz modified_at
  }
  Membership {
    bigint id PK
    bigint organisation_id FK
    bigint user_id FK "unique with organisation_id"
    varchar role "owner | admin | developer | support"
    timestamptz joined_at
    timestamptz last_active_at
  }
```

```mermaid
erDiagram
  Organisation ||--o{ Invitation : "issues"
  Organisation ||--o| Sandbox : "is provisioned"

  Invitation {
    bigint id PK
    bigint organisation_id FK
    varchar email "one open invite per org + email"
    varchar role "owner | admin | developer | support"
    varchar token UK "redeemed through the invite URL"
    bigint invited_by_id FK "User"
    timestamptz created_at
    timestamptz expires_at
    timestamptz accepted_at
    timestamptz revoked_at
  }
  Sandbox {
    bigint id PK
    bigint organisation_id FK "unique, 1:1"
    varchar status "requested | provisioning | ready | failed"
    varchar facility_name
    bool is_facility_empty
    varchar job_id "Care provisioning job"
    jsonb result "server, facility, users"
    text error
    bigint requested_by_id FK "User"
    bigint provisioned_by_id FK "User"
    timestamptz requested_at
    timestamptz provisioned_at
    timestamptz modified_at
  }
```

- Membership is unique per (organisation, user); role orders the team roster owner → admin → developer → support.
- An Invitation is unique per (organisation, email) while it is still open (neither accepted nor revoked); the token is what the invite link carries.
- Sandbox is one-to-one with Organisation: the Care sandbox provisioned by the OHC team, with the provisioning result kept as JSON.
- verified_by, invited_by, requested_by and provisioned_by point at users_user and are nulled if that user is deleted.

## ABDM sandbox and certification

_Apps: abdm · 7 tables_

A Product is the thing an integrator certifies; its sandbox_id (SBX-2026-00001) names it everywhere. A registered product holds one Credential for the gateway and one ComplianceRecord per applied milestone. Reviewers never work on those rows directly: every decision goes through a ReviewItem, whose item_type says which of three subjects it is about.

```mermaid
erDiagram
  Organisation ||--o{ Product : "registers"
  Product ||--o| Credential : "holds"
  Product ||--o{ ComplianceRecord : "one per track and milestone"

  Product {
    bigint id PK
    bigint organisation_id FK
    varchar sandbox_id UK "SBX-2026-00001"
    varchar name
    text description
    varchar category "6 categories, see vocabulary"
    varchar solution_type "clinical_hmis | eua | health_locker"
    jsonb applied_tracks "track codes"
    jsonb applied_milestones "TRACK:CODE keys"
    varchar registration_status "pending | registered | sent_back"
    date registered_on
    bigint registered_by_id FK "User"
    text sent_back_reason
    bigint created_by_id FK "User"
    timestamptz created_at
    timestamptz updated_at
  }
  Credential {
    bigint id PK
    bigint product_id FK "unique, 1:1"
    varchar client_id UK
    text secret_encrypted "encrypted at rest"
    varchar gateway_base_url
    varchar callback_url
    varchar bridge_url
    timestamptz issued_on
    date rotation_due "issued_on + rotation days"
    timestamptz rotated_on
    timestamptz revoked_on
    smallint callback_status_code
    int callback_latency_ms
    timestamptz callback_checked_at
    varchar callback_error
    smallint callback_failure_streak "alert threshold in settings"
    timestamptz created_at
    timestamptz updated_at
  }
  ComplianceRecord {
    bigint id PK
    bigint product_id FK "unique with track + milestone"
    varchar track_code "HI-CM | UHI | NHCX | PHR | HealthLocker"
    varchar milestone_code "M1 | M2 | M3 | M4 | UHI1 | PHR1"
    varchar status "6 states, see vocabulary"
    date start_date
    date end_date
    date demo_date
    varchar wasa_agency "web application security assessor"
    date wasa_date
    varchar functional_certificate "file"
    varchar functional_report "file"
    timestamptz submitted_on
    smallint resubmission_count
    date approved_on
    bigint approved_by_id FK "User"
    text decision_note
    timestamptz sent_back_on
    bigint sent_back_by_id FK "User"
    text sent_back_reason
    timestamptz created_at
    timestamptz updated_at
  }
```

```mermaid
erDiagram
  Organisation ||--o{ ReviewItem : "is the subject of"
  Product |o..o{ ReviewItem : "registration, exit"
  ComplianceRecord |o..o| ReviewItem : "exit request"
  ReviewItem ||--o{ ReviewQuery : "raises"
  ReviewItem ||--o{ ReviewHistory : "records"

  ReviewItem {
    bigint id PK
    varchar reference UK "REV-2026-00017"
    varchar item_type "verification | registration | exit"
    bigint organisation_id FK "always set"
    bigint product_id FK "registration and exit items"
    bigint compliance_id FK "exit items only, unique"
    varchar status "6 states, see vocabulary"
    timestamptz submitted_on
    smallint resubmission_count
    bigint assignee_id FK "User"
    bigint assigned_by_id FK "User"
    date decided_on
    bigint decided_by_id FK "User"
    text decision_note
    timestamptz created_at
    timestamptz updated_at
  }
  ReviewQuery {
    bigint id PK
    bigint item_id FK
    varchar field_key "form, or one field key"
    varchar field_label
    text question
    bigint raised_by_id FK "User"
    timestamptz raised_at
    varchar status "open | answered | resolved"
    text reply
    bigint replied_by_id FK "User"
    timestamptz replied_at
    timestamptz resolved_at
    bigint resolved_by_id FK "User"
  }
  ReviewHistory {
    bigint id PK
    bigint item_id FK
    bigint actor_id FK "User"
    varchar kind "11 kinds, see vocabulary"
    varchar title
    text description
    jsonb payload
    timestamptz created_at
  }
  AuditLog {
    bigint id PK
    bigint actor_id FK "User"
    varchar model_label "app.Model of the row touched"
    varchar object_pk
    varchar object_repr
    varchar action
    jsonb diff
    timestamptz created_at
  }
```

- ReviewItem always carries organisation_id; product_id is set for product_registration and exit_request items; compliance_id only for exit_request items, where it is one-to-one.
- Partial unique constraints keep one organisation_verification item per organisation and one product_registration item per product.
- ComplianceRecord is unique per (product, track_code, milestone_code). PHR:M1 is an alias of HI-CM:M1, so the canonical HI-CM row is the only one stored.
- ReviewQuery targets one field_key (or the whole form) and moves open → answered → resolved; ReviewHistory and AuditLog are append-only.
- AuditLog has no foreign key to the row it describes: model_label plus object_pk, so entries survive deletes.
- Status changes happen only in abdm/services.py; models only describe themselves.

## Application engine

_Apps: experiences · 7 tables_

A code-defined application (registry key application_type, for example the ABDM production-access flow) runs as an ApplicationInstance. Each form the applicant completes is stored as an immutable ApplicationFormSubmission revision, files live beside it as versioned attachments, and every mutation lands in ApplicationEvent.

```mermaid
erDiagram
  Organisation ||--o{ ApplicationInstance : "applies, PROTECT"
  ApplicationInstance ||--o{ ApplicationFormSubmission : "form revisions"
  ApplicationFormSubmission ||--o{ ApplicationAttachment : "files"

  ApplicationInstance {
    bigint id PK
    varchar reference UK "ABDM-26-K7Q2MX"
    varchar application_type "experience registry key"
    varchar title
    bigint organisation_id FK "PROTECT"
    bigint created_by_id FK "User, PROTECT"
    varchar status "defined per application type"
    jsonb metadata
    jsonb outcome
    timestamptz submitted_at
    timestamptz decided_at
    bigint decided_by_id FK "User"
    timestamptz created_at
    timestamptz updated_at
  }
  ApplicationFormSubmission {
    bigint id PK
    bigint application_id FK
    varchar form_key
    varchar status "completed | needs_changes"
    jsonb data "validated form values"
    jsonb field_schema
    jsonb metadata
    smallint schema_version
    int revision
    int submission_number
    bool is_current "one current per form_key"
    date valid_until
    bigint submitted_by_id FK "User, PROTECT"
    timestamptz submitted_at
    timestamptz updated_at
  }
  ApplicationAttachment {
    bigint id PK
    bigint submission_id FK
    varchar field_key
    varchar file
    varchar original_name
    varchar content_type
    bigint size
    bool is_current
    bigint uploaded_by_id FK "User, PROTECT"
    timestamptz created_at
  }
```

```mermaid
erDiagram
  ApplicationInstance ||--o{ ApplicationAccess : "grants"
  User ||--o{ ApplicationAccess : "holds"
  ApplicationInstance ||--o{ ApplicationQueryThread : "queries"
  ApplicationFormSubmission |o..o{ ApplicationQueryThread : "about"
  ApplicationQueryThread ||--o{ ApplicationQueryMessage : "messages"
  ApplicationInstance ||--o{ ApplicationEvent : "audit trail"
  ApplicationFormSubmission |o..o{ ApplicationEvent : "about"

  ApplicationAccess {
    bigint id PK
    bigint application_id FK "unique with user_id"
    bigint user_id FK
    varchar role_key
    jsonb direct_permissions
    bigint granted_by_id FK "User"
    timestamptz created_at
    timestamptz updated_at
  }
  ApplicationQueryThread {
    bigint id PK
    bigint application_id FK
    bigint submission_id FK "nullable"
    varchar subject
    varchar status "awaiting_applicant | awaiting_reviewer | resolved"
    bigint opened_by_id FK "User, PROTECT"
    bigint assigned_to_id FK "User"
    date due_at
    timestamptz resolved_at
    timestamptz created_at
    timestamptz updated_at
  }
  ApplicationQueryMessage {
    bigint id PK
    bigint thread_id FK
    bigint author_id FK "User, PROTECT"
    text body
    varchar kind "message | event"
    bool is_internal "reviewer-only note"
    timestamptz created_at
  }
  ApplicationEvent {
    bigint id PK
    bigint application_id FK
    bigint submission_id FK "nullable"
    bigint actor_id FK "User"
    varchar kind "6 kinds, see vocabulary"
    varchar title
    text description
    varchar action_key
    varchar status_before
    varchar status_after
    jsonb payload
    timestamptz created_at
  }
```

- Submissions are unique per (application, form_key, submission_number, revision), and only one row per (application, form_key) may be current.
- ApplicationAccess grants one role plus direct permissions per (application, user); effective permissions are the union.
- Query threads may point at the submission they concern; messages flagged is_internal are visible to reviewers only.
- created_by, submitted_by, uploaded_by, opened_by and message authors are PROTECTed: a user who authored application data cannot be deleted.
- Organisation is PROTECTed by ApplicationInstance too, unlike everywhere else in the model.

## Support and events

_Apps: support · events · 4 tables_

A Ticket is one conversation between an organisation and the Care team, optionally pinned to a Product and a certification track; its thread is a list of TicketMessage rows, some of them recorded status changes rather than replies. Events are the partner calendar; a registration is one user on one event.

```mermaid
erDiagram
  Organisation ||--o{ Ticket : "opens"
  Product |o..o{ Ticket : "concerns"
  Ticket ||--o{ TicketMessage : "thread"
  Event ||--o{ EventRegistration : "sign-ups"
  User ||--o{ EventRegistration : "registers"

  Ticket {
    bigint id PK
    varchar reference UK "TKT-2001"
    bigint organisation_id FK
    varchar subject
    varchar category "sandbox | api | certification | deployment | billing"
    varchar priority "high | medium | low"
    varchar status "open | awaiting_vendor | resolved | closed"
    bigint created_by_id FK "User"
    bigint assignee_id FK "User"
    varchar linked_facility
    bigint product_id FK "nullable"
    varchar track "HI-CM | UHI | NHCX | PHR | HealthLocker"
    timestamptz created_at
    timestamptz updated_at
    timestamptz first_responded_at
    timestamptz resolved_at "never before created_at"
  }
  TicketMessage {
    bigint id PK
    bigint ticket_id FK
    bigint author_id FK "User"
    varchar kind "reply | event"
    text body
    bool from_ohc_team
    varchar attachment "file"
    timestamptz created_at
  }
  Event {
    bigint id PK
    varchar title
    varchar slug UK
    varchar kind "office_hours | webinar | ama | workshop"
    varchar summary
    text description
    timestamptz starts_at
    timestamptz ends_at "after starts_at"
    varchar location "blank means online"
    varchar join_url
    varchar materials_url
    varchar recording_url
    timestamptz published_at "null while a draft"
    bigint created_by_id FK "User"
    timestamptz created_at
    timestamptz updated_at
  }
  EventRegistration {
    bigint id PK
    bigint event_id FK "unique with user_id"
    bigint user_id FK
    timestamptz created_at
    timestamptz reminded_at "day-before reminder"
  }
```

- Ticket references are sequential from TKT-2001, derived from the highest existing number so a deleted ticket never lends its reference.
- A check constraint keeps resolved_at at or after created_at; first_responded_at feeds the response-time figures.
- Deleting a product leaves its tickets in place with product_id nulled.
- An Event is public once published_at is set; ends_at must be after starts_at. Registrations are unique per (event, user) and reminded once, the day before.

## Vocabulary

| Column | Values |
|---|---|
| `Organisation.entity_type` | private_company · government_body · sole_proprietorship · trust_or_society · section_8 · llp |
| `Organisation.category` | india_entity · foreign_with_indian_subsidiary · academic |
| `Organisation.verification_status` | pending → verified | sent_back |
| `Membership.role, Invitation.role` | owner · admin · developer · support |
| `Sandbox.status` | requested → provisioning → ready | failed |
| `Product.category` | hmis · lmis · phr_app · health_locker · payer_tpa · other |
| `Product.solution_type` | clinical_hmis · eua · health_locker |
| `Product.registration_status` | pending → registered | sent_back |
| `ComplianceRecord.status` | locked → open → in_progress → under_review → query_raised | approved |
| `ReviewItem.item_type` | organisation_verification · product_registration · exit_request |
| `ReviewItem.status` | new → in_review → query_raised | approved | sent_back | withdrawn |
| `ReviewQuery.status` | open → answered → resolved |
| `ReviewHistory.kind` | submitted · resubmitted · started · assigned · query_raised · query_answered · query_resolved · approved · sent_back · withdrawn · milestone |
| `ApplicationFormSubmission.status` | completed · needs_changes |
| `ApplicationQueryThread.status` | awaiting_applicant · awaiting_reviewer · resolved |
| `ApplicationQueryMessage.kind` | message · event |
| `ApplicationEvent.kind` | created · form_submitted · action · status_changed · access_changed · query |
| `Ticket.category` | sandbox · api · certification · deployment · billing |
| `Ticket.priority` | high · medium · low |
| `Ticket.status` | open ⇄ awaiting_vendor → resolved → closed |
| `TicketMessage.kind` | reply · event |
| `Event.kind` | office_hours · webinar · ama · workshop |

### Tracks and milestones

| Track | Name | Milestones |
|---|---|---|
| `HI-CM` | Health information exchange and consent manager | M1 · M2 · M3 · M4 |
| `UHI` | Unified health interface | UHI1 |
| `NHCX` | National health claims exchange | no milestones defined yet |
| `PHR` | Personal health records | M1 (alias of HI-CM:M1) · PHR1 |
| `HealthLocker` | Health locker | PHR1 |

### Identifiers

| Column | Example | Rule |
|---|---|---|
| `Product.sandbox_id` | `SBX-2026-00001` | SBX-year-sequence; the sequence restarts each year and is derived from the highest existing id. |
| `ReviewItem.reference` | `REV-2026-00017` | Same scheme with the REV prefix. |
| `Ticket.reference` | `TKT-2001` | Sequential from a seed of 2000, so the first ticket is never TKT-1. |
| `ApplicationInstance.reference` | `ABDM-26-K7Q2MX` | Definition prefix (APP by default, ABDM for the production-access flow), two-digit year, six random characters. |

## Deletion rules

- **Delete an organisation.** Cascades to memberships, invitations, the sandbox, products (and through them credentials, compliance records and review items with their queries and history) and tickets with their messages. Blocked while any ApplicationInstance references it.
- **Delete a user.** Blocked while they authored application data (instances, submissions, attachments, query threads, query messages). Otherwise memberships, event registrations and access grants cascade, and every actor column (verified_by, assignee, approved_by, raised_by, actor …) is set to NULL so history keeps reading.
- **Delete a product.** Cascades to its credential, compliance records and review items. Tickets survive with product_id nulled.
- **Delete a review item.** Cascades to its queries and history. The compliance record it reviewed stays.

## Not drawn

Third-party tables installed alongside: allauth (account_emailaddress, account_emailconfirmation, socialaccount_*, mfa_authenticator), django_celery_beat_*, auth_group, auth_permission, django_admin_log, django_content_type, django_session, django_site. `users_user` also carries Django's `groups` and `user_permissions` many-to-many tables.
