from ohc_experience.experiences.models import Product
from ohc_experience.users.tests.factories import UserFactory


def product_for(organisation):
    """Every ticket names a product; tests only need one per organisation."""
    return Product.objects.filter(organisation=organisation).first() or (
        Product.objects.create(
            organisation=organisation,
            name="Sandbox HMIS",
            description="Hospital information system under test.",
            created_by=UserFactory.create(),
        )
    )
