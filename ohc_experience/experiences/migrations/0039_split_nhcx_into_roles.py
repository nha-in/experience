"""NHCX1 becomes a choice of role: Payer on M1 and M3, Provider on M1 and M2, or
Patient app on P1.

NHCX1 recorded no role, so the product picks one: a PHR product is a patient
app, an Insurance one a payer, since Insurance fixes the M1 and M3 a payer
builds on, and every other one a provider. A request keeps its drafts and
evidence; only its key, title and prerequisites move to the role.
"""

from django.db import migrations

ROLES = ("nhcx_payer", "nhcx_provider", "nhcx_patient_app")
PREDECESSORS = {
    "nhcx_payer": ("m1", "m3"),
    "nhcx_provider": ("m1", "m2"),
    "nhcx_patient_app": ("p1",),
}
#: `workflows.project_product` writes this title when a request is opened.
TITLES = {
    "nhcx1": "NHCX1 - Claims exchange flows",
    "nhcx_payer": "Payer - Claims exchange as a payer",
    "nhcx_provider": "Provider - Claims exchange as a provider",
    "nhcx_patient_app": "Patient app - Claim updates in a patient app",
}


def role_for(product):
    if any(value.startswith("PHR:") for value in product.applied_milestones):
        return "nhcx_patient_app"
    if "insurance" in product.solution_type:
        return "nhcx_payer"
    return "nhcx_provider"


def forwards(apps, schema_editor):
    product_model = apps.get_model("experiences", "Product")
    for product in product_model.objects.filter(experience_type="abdm"):
        has_milestone = product.milestones.filter(key="nhcx1").exists()
        if not has_milestone and "NHCX:nhcx1" not in product.applied_milestones:
            continue
        role = role_for(product)
        _move(apps, product, "nhcx1", role, PREDECESSORS[role])
        _swap_selection(apps, product, "NHCX:nhcx1", f"NHCX:{role}")


def backwards(apps, schema_editor):
    product_model = apps.get_model("experiences", "Product")
    for product in product_model.objects.filter(experience_type="abdm"):
        for role in ROLES:
            # NHCX1 opened on whichever identity milestone the product carries.
            _move(apps, product, role, "nhcx1", ("m1", "p1"))
            _swap_selection(apps, product, f"NHCX:{role}", "NHCX:nhcx1")


def _move(apps, product, old, new, predecessors):
    dependency = apps.get_model("experiences", "ApplicationDependency")
    for row in product.milestones.filter(key=old).select_related("application"):
        row.key = new
        row.save(update_fields=["key"])
        application = row.application
        application.title = TITLES[new]
        application.metadata["milestone"] = new
        application.save(update_fields=["title", "metadata"])
        dependency.objects.filter(application=application).delete()
        for prerequisite in product.milestones.filter(key__in=predecessors):
            dependency.objects.create(
                application=application,
                depends_on_id=prerequisite.application_id,
            )


def _swap_selection(apps, product, old, new):
    if old in product.applied_milestones:
        product.applied_milestones = [
            new if value == old else value for value in product.applied_milestones
        ]
        product.save(update_fields=["applied_milestones"])
    # The registration form keeps its own copy, which is what reopening it reads.
    submission = apps.get_model("experiences", "FormSubmission")
    for pk, data in submission.objects.filter(form__product=product).values_list(
        "pk",
        "data",
    ):
        values = data.get("applied_milestones") if isinstance(data, dict) else None
        if not isinstance(values, list) or old not in values:
            continue
        applied = [new if value == old else value for value in values]
        submission.objects.filter(pk=pk).update(
            data={**data, "applied_milestones": applied},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0038_constraint_error_messages"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
