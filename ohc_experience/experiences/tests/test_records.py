import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError

from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.registry import ExperienceRegistry
from ohc_experience.experiences.services import create_application
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(owner_membership):
    return Product.objects.create(
        organisation=owner_membership.organisation,
        name="Shared evidence product",
        product_type="hmis",
        description="Sandbox test product",
        created_by=owner_membership.user,
    )


def start(product, user=None):
    return create_application(
        application_type="abdm_sandbox_exit",
        product=product,
        user=user or product.created_by,
    )


def test_creating_applications_reuses_the_product_form_and_pins_a_revision(product):
    first = start(product)
    use = first.form_uses.get()
    snapshot = FormSubmission.objects.create(
        form=use.form,
        origin_application=first,
        data={"evidence": "original"},
        submitted_by=product.created_by,
    )
    second = start(product)
    assert second.form_uses.get().form == use.form
    assert second.form_uses.get().selected_submission == snapshot
    assert first.reference != second.reference


def test_form_reuse_is_scoped_to_the_product(product):
    first = start(product)
    other = Product.objects.create(
        organisation=product.organisation,
        name="Other product",
        product_type="hmis",
        description="Separate scope",
        created_by=product.created_by,
    )
    assert start(other).form_uses.get().form_id != first.form_uses.get().form_id


def test_only_members_can_create_applications(product):
    outsider = UserFactory()
    with pytest.raises(PermissionDenied):
        start(product, outsider)
    teammate = MembershipFactory(organisation=product.organisation)
    assert start(product, teammate.user).created_by == teammate.user


def test_form_use_rejects_cross_product_links_and_foreign_submissions(product):
    first = start(product)
    other_member = MembershipFactory()
    other_product = Product.objects.create(
        organisation=other_member.organisation,
        name="Private product",
        product_type="hmis",
        description="Other organisation",
        created_by=other_member.user,
    )
    other = start(other_product)
    use = ApplicationFormUse(
        application=first,
        form=other.form_uses.get().form,
        form_key="sandbox_exit_evidence",
    )
    with pytest.raises(ValidationError, match="organisation"):
        use.clean()
    use.form = first.form_uses.get().form
    use.selected_submission = FormSubmission.objects.create(
        form=other.form_uses.get().form,
        origin_application=other,
        submitted_by=other_member.user,
    )
    with pytest.raises(ValidationError, match="selected submission"):
        use.clean()


def test_application_dependencies_reject_cycles(product):
    first, second = start(product), start(product)
    ApplicationDependency.objects.create(application=second, depends_on=first)
    link = ApplicationDependency(application=first, depends_on=second)
    with pytest.raises(ValidationError, match="cycle"):
        link.clean()


def test_registry_rejects_duplicate_application_keys_and_form_keys():
    class Evidence(ApplicationFormDefinition):
        key = "evidence"
        name = "Evidence"

    class Example(ApplicationDefinition):
        key = "example"
        name = "Example"
        forms = (Evidence,)

    registry = ExperienceRegistry()
    registry.register(Example)
    with pytest.raises(ImproperlyConfigured, match="already registered"):
        registry.register(Example)
    Example.forms = (Evidence, Evidence)
    with pytest.raises(ImproperlyConfigured, match="uniquely named"):
        Example.validate()
