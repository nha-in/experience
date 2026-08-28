# Experience manager

The experience manager keeps workflow definitions in Python and workflow history in
stable database rows.

## Main pieces

- `definitions.py` defines the extension API for statuses, permissions, roles, forms,
  and actions.
- `registry.py` maps a persisted `application_type` to its Python definition.
- `models.py` stores application instances, validated form JSON, versioned uploads,
  application-scoped access grants, query threads, and append-only audit events.
- `services.py` is the only write boundary for form submissions, state transitions,
  permissions, queries, and outcomes.
- `abdm/` is the kitchen-sink ABDM production-access example.

An application definition exposes every permission and role it supports. Effective
permissions are the union of the assigned role and any valid direct permissions.
Applicant-side access is restricted to members of the owning organisation; review
access is restricted to OHC team users. Available form and action states are computed
from these effective permissions and the current application state.

## Add an experience

1. Create static Django `Form` classes for validated inputs.
2. Subclass `ApplicationFormDefinition` for each form and declare dependencies.
3. Subclass `ApplicationAction` for each state transition or outcome.
4. Subclass `ApplicationDefinition`, declare statuses, permissions, roles, forms, and
   actions, then decorate it with `@registry.register`.
5. Import the definition from `ExperiencesConfig.ready()`.

Views remain generic. A registered definition automatically appears in application
creation, form workspaces, permission-aware actions, applicant dashboards, and the OHC
review console.

### Form dependencies

Dependencies are declared on the static form definition. A dependent form is omitted
from the applicant workspace and its URL remains unavailable until every dependency has
a completed submission. The ABDM flow uses this throughout; for example, Security and
privacy appears only after Technical readiness is complete:

```python
class SecurityCompliance(ApplicationFormDefinition):
    key = "security_compliance"
    form_class = SecurityComplianceForm
    dependencies = (TechnicalReadiness.key,)
```

`ApplicationFormDefinition.is_visible()` owns this rule, so a custom UI cannot bypass
it by linking directly to the form.

## HTMX

Every experience interaction keeps an ordinary Django `method`, `action`, and redirect
fallback. HTMX progressively enhances application filters, form validation, workflow
actions, query conversations, and access management. Fragment responses update only
the affected workspace; flash messages and query headers use out-of-band swaps, while
successful operations that leave a workspace use `HX-Redirect`.

## Demo data

Run:

```console
python manage.py seed_experience_demo
```

The command creates a partially completed applicant workspace and a fully submitted
review workspace. It prints verified applicant, contributor, and OHC decision-maker
credentials. Re-running it is idempotent; `--fresh` removes only these two seeded
applications and recreates them.
