# Experience manager

The experience manager keeps workflow definitions in Python and workflow history in
stable database rows.

## Main pieces

- `definitions.py` defines the extension API for statuses, permissions, roles, forms,
  and actions.
- `registry.py` maps a persisted `application_type` to its Python definition.
- `models.py` stores organisation-owned products, independent form records,
  immutable form submissions, application-to-form links, structured product outcomes,
  application prerequisites, access grants, query threads, and audit events.
- `services.py` is the only write boundary for form submissions, state transitions,
  permissions, queries, and outcomes.
- `abdm/` is the kitchen-sink ABDM production-access example.

Every application belongs to exactly one product. Product identity and deployment
details live once on that product, while each related application keeps its own
status, permissions, queries, and selected form revisions. Applications may depend
on earlier applications for the same product; submission stays unavailable until
every prerequisite reaches a status its own definition declares as
dependency-satisfying.

## Independent forms and reuse

`FormRecord` is the durable identity of a form. `FormSubmission` stores immutable
JSON revisions and versioned attachments against that identity. `ApplicationFormUse`
links an application to a form and pins the exact submission selected for that
application, so later edits remain visible in history without silently changing an
older application pack.

Each static form definition declares a reuse scope:

```python
class OrganisationProfile(ApplicationFormDefinition):
    key = "organisation_profile"
    reuse_scope = FormReuseScope.ORGANISATION


class ApplicationPlan(ApplicationFormDefinition):
    key = "application_plan"
    reuse_scope = FormReuseScope.PRODUCT


class Declaration(ApplicationFormDefinition):
    key = "declaration"
    reuse_scope = FormReuseScope.APPLICATION
```

When an application starts, the service materializes all of its form links. It reuses
the current submission for an existing organisation- or product-scoped record and
creates a private record for an application-only form. Editing creates a new immutable
revision; renewal creates a fresh submission number.

## Product outcomes

`ProductOutcome` records a typed, structured result issued by an application to its
product. A definition can issue initial outcomes from `initial_product_outcomes()` or
return `ProductOutcomeSpec` values from an application action. Field schemas provide
stable labels and secret-value hints for the generic product workspace. The ABDM
example issues sandbox credentials when an application starts and production access
when a decision maker approves it.

An application definition exposes every permission and role it supports. Effective
permissions are the union of the assigned role and any valid direct permissions.
Applicant-side access is restricted to members of the owning organisation; review
access is restricted to OHC team users. Every OHC team user has implicit read-only
observer access across the review console, while reviewer and decision permissions
remain explicit application grants. Available form and action states are computed from
these effective permissions and the current application state.

## Add an experience

1. Create static Django `Form` classes for validated inputs.
2. Subclass `ApplicationFormDefinition` for each form and declare dependencies.
3. Subclass `ApplicationAction` for each state transition or outcome.
4. Subclass `ApplicationDefinition`, declare statuses, permissions, roles, forms, and
   actions, then decorate it with `@registry.register`.
5. Import the definition from `ExperiencesConfig.ready()`.

Views remain generic. A registered definition automatically appears on each product
workspace and powers application creation, form workspaces, permission-aware actions,
applicant dashboards, and the OHC review console.

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

### Application dependencies

An applicant selects zero or more prerequisite applications when starting another
workflow from a product. `ApplicationDependency` rejects self references, cycles, and
cross-product links. The application action definition performs the final submission
gate, so direct requests cannot bypass an unmet prerequisite.

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

The command creates one product with a fully submitted review application and a
partially completed follow-up application that depends on it. It prints verified
applicant, contributor, and OHC decision-maker credentials. Re-running it is
idempotent; `--fresh` removes only this seeded product and its applications before
recreating them.
