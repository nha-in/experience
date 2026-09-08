"""A non-healthcare implementation used to exercise the public extension API."""

from django import forms
from django.utils import timezone

from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.definitions import MilestoneDefinition
from ohc_experience.experiences.definitions import OutcomeDefinition
from ohc_experience.experiences.definitions import ProgramDefinition
from ohc_experience.experiences.definitions import TrackDefinition
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.models import FormReuseScope
from ohc_experience.experiences.workflows import project_product


class SupplierForm(ReviewForm):
    supplier_name = forms.CharField()


class EquipmentForm(ReviewForm):
    equipment_name = forms.CharField()
    summary = forms.CharField()
    checks = forms.MultipleChoiceField(
        choices=[("Quality:inspection", "Inspection"), ("Quality:release", "Release")],
    )


class InspectionForm(ReviewForm):
    report_reference = forms.CharField()
    score = forms.IntegerField(min_value=0, max_value=100)


class SupplierVerification(ApplicationFormDefinition):
    key = "supplier_identity"
    name = "Supplier identity"
    reuse_scope = FormReuseScope.ORGANISATION
    form_class = SupplierForm
    allow_approved_updates = True

    @classmethod
    def on_submit(cls, item, data, actor):
        item.organisation.name = data["supplier_name"]
        item.organisation.onboarded_at = timezone.now()
        item.organisation.save()

    @classmethod
    def on_approve(cls, item, actor):
        item.organisation.set_verification("verified")
        return ()


class EquipmentRegistration(ApplicationFormDefinition):
    key = "equipment_registration"
    name = "Equipment registration"
    form_class = EquipmentForm

    @classmethod
    def on_submit(cls, item, data, actor):
        project_product(
            item,
            actor,
            product_values=SupplierQuality.product_values(data),
            solution_type="equipment",
            selections=data["checks"],
        )


class InspectionEvidence(ApplicationFormDefinition):
    key = "inspection_report"
    name = "Inspection report"
    form_class = InspectionForm
    allow_reuse = True

    @classmethod
    def on_approve(cls, item, actor):
        return (
            OutcomeDefinition(
                key="quality_certificate",
                name="Quality certificate",
                data={"score": item.selected_submission.data["score"]},
            ),
        )


class EquipmentApplication(ApplicationDefinition):
    key = "equipment_application"
    name = "Equipment registration"
    forms = (EquipmentRegistration,)

    @classmethod
    def on_start(cls, application, actor):
        return (
            OutcomeDefinition(
                key="receipt",
                name="Registration receipt",
                data={"reference": application.reference},
            ),
        )


class InspectionApplication(ApplicationDefinition):
    key = "inspection_application"
    name = "Quality inspection"
    forms = (InspectionEvidence,)


class SupplierQuality(ProgramDefinition):
    key = "supplier_quality"
    name = "Supplier Quality Portal"
    short_name = "Quality"
    product_reference_prefix = "QA"
    organisation_form = SupplierVerification
    product_application = EquipmentApplication
    milestone_application = InspectionApplication
    # Intentionally not in dependency order.
    milestones = {
        "release": MilestoneDefinition("release", "REL", "Release", "inspection"),
        "inspection": MilestoneDefinition("inspection", "INS", "Inspection"),
    }
    tracks = (
        TrackDefinition(
            "Quality",
            "Quality assurance",
            "Supplier quality checks.",
            ("inspection", "release"),
        ),
    )

    @classmethod
    def product_values(cls, data):
        return {
            "name": data["equipment_name"],
            "description": data["summary"],
            "product_type": "equipment",
        }
