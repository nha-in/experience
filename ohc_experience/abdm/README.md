# ABDM Implementation

This is a regular Python package, not a Django app. It has no AppConfig, models,
migrations, views or URL configuration. All persistence and workflow execution
belong to `experiences`.

- `forms.py`: ABDM organisation, product and exit-evidence fields and validation.
- `catalog.py`: ABDM tracks, milestones and prerequisite definitions.
- `definitions.py`: form/application identities, ABDM eligibility and review hooks,
  product mappings, structured approval outcomes and portal labels.
- `gateway.py`: ABDM credential eligibility and provider/demo integration.
- `demo.py`: the ABDM data builder invoked by `seed_experience_demo`.
- `tests/`: domain-specific regression coverage.

The dependency direction is `abdm -> experiences`. Settings select
`ohc_experience.abdm.definitions.ABDM`; the engine discovers it through its registry,
never by importing this package directly. Stable persisted form/application keys
are retained so existing submissions and outcomes remain accessible.

See [the engine contracts](../experiences/README.md) and
[the portal setup guide](../../docs/abdm_sandbox.md).
