# Shared Application and Form Records

This app supplies the persistence and upload primitives used by the ABDM
Developer Sandbox. Workflow policies and UI live in `ohc_experience/sandbox`;
there is no separate generic workflow console or REST API.

## Responsibilities

- `definitions.py` declares application identities and their form reuse scopes.
- `registry.py` resolves persisted application types. `sandbox/definitions.py`
  registers product registration and sandbox exit applications.
- `models.py` stores products, applications, independent forms, submission
  revisions, pinned form links, application dependencies and product outcomes.
- `forms.py` and `fields.py` validate uploads, retained files and removal requests.
- `services.py` creates applications, links reusable records, snapshots field
  schemas and copies/stores attachments for new submission revisions.
- `sandbox/services.py` owns permissions, milestone prerequisites, review state
  transitions, queries, submission history and audit events.

## Independent Forms

`FormRecord` is a durable form identity. A definition declares an organisation,
product or application reuse scope. Creating an application materializes its
`ApplicationFormUse` links, reusing the current submission within that scope.
Each link pins an exact `FormSubmission`; later edits do not silently replace
another application's evidence.

Submissions store JSON answers, the field schema/version, occurrence and revision
numbers, and attachment metadata. The sandbox workflow creates a new revision
when editing evidence, or a new occurrence when submitting it under another
application. History renders the saved schema even if the Python form changes.
Retained attachments are linked into the new revision without modifying earlier
versions. Downloads are authorised by the sandbox views and streamed from private
MinIO locally or S3 in production.

`ApplicationDependency` rejects cross-product links, self references and cycles.
The fixed milestone catalog and sandbox service enforce prerequisite approvals
before submission.

## Outcomes and Access

`ProductOutcome` stores structured application results at product scope. The
sandbox workflow records milestone decisions and credential references; encrypted
credential secrets live separately in `SandboxCredential`.

Organisation memberships govern integrator access. Review items have explicit
reviewer assignments, and only the assignee or a superuser can decide. Team
invitation and role-management rules remain in the organisations app. The retired
generic access grants, query tables and audit tables are replaced by these active
membership, review-query and append-only sandbox audit models.

## Demo and Verification

See [the sandbox guide](../../docs/abdm_sandbox.md) for setup, demo accounts,
configuration and test commands. `seed_sandbox_demo --reset` creates the current
demo and is deliberately destructive; never run it against data to retain.

Migration history is retained. Cleanup migrations remove obsolete tables without
resetting the current portal's products, reviews, submissions or attachments.
