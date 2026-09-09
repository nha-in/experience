# Experience manager

The experience manager keeps workflow definitions in Python and workflow history in
stable database rows. It is an engine only: nothing in this app knows about ABDM or
any other programme, and the registry ships empty until another app registers a
definition.

## Main pieces

- `definitions.py` defines the extension API for statuses, permissions, roles, forms,
  and actions.
- `registry.py` maps a persisted `application_type` to its Python definition.
- `models.py` stores application instances, validated form JSON, versioned uploads,
  application-scoped access grants, query threads, and append-only audit events.
- `services.py` is the only write boundary for form submissions, state transitions,
  permissions, queries, and outcomes.
- `forms.py` holds `ExperienceForm`, the base class every registered form subclasses,
  plus the engine's own filter, reply, and access forms.
- `uploads.py` holds upload validation and size limits, and `ModelFileFormMixin`,
  which gives an ordinary `ModelForm` the existing-file shape the shared upload
  widget expects.
- `tests/sample.py` is the reference implementation: a small experience that
  exercises every engine feature, registered only from the tests' conftest.

An application definition exposes every permission and role it supports. Effective
permissions are the union of the assigned role and any valid direct permissions.
Applicant-side access is restricted to members of the owning organisation; review
access is restricted to OHC team users. Available form and action states are computed
from these effective permissions and the current application state.

## Add an experience

1. Create static Django `Form` classes for validated inputs, subclassing
   `ExperienceForm`.
2. Subclass `ApplicationFormDefinition` for each form and declare dependencies.
3. Subclass `ApplicationAction` for each state transition or outcome.
4. Subclass `ApplicationDefinition`, declare statuses, permissions, roles, forms, and
   actions, then register it with `registry.register`.
5. Import the definition from your own app's `AppConfig.ready()` so registration
   happens at start-up.

Views remain generic. A registered definition automatically appears in application
creation, form workspaces, permission-aware actions, applicant dashboards, and the OHC
review console.

### Form dependencies

Dependencies are declared on the static form definition. A dependent form is omitted
from the applicant workspace and its URL remains unavailable until every dependency has
a completed submission. In the sample, compliance evidence appears only after
technical readiness is complete:

```python
class Compliance(ApplicationFormDefinition):
    key = "compliance"
    form_class = ComplianceForm
    dependencies = (Readiness.key,)
```

`ApplicationFormDefinition.is_visible()` owns this rule, so a custom UI cannot bypass
it by linking directly to the form. `is_applicable()` switches a form off entirely
based on earlier answers, and repeatable forms with a `valid_until_field` keep every
renewal as its own submission.

## HTMX

Every experience interaction keeps an ordinary Django `method`, `action`, and redirect
fallback. HTMX progressively enhances application filters, form validation, workflow
actions, query conversations, and access management. Fragment responses update only
the affected workspace; flash messages and query headers use out-of-band swaps, while
successful operations that leave a workspace use `HX-Redirect`.
