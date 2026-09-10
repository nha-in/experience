from __future__ import annotations

from factory import Faker
from factory import post_generation
from factory.django import DjangoModelFactory

from ohc_experience.users.models import User


class UserFactory(DjangoModelFactory[User]):
    email = Faker("email")
    name = Faker("name")

    @post_generation
    def password(self: User, create: bool, extracted: str | None, **kwargs):  # noqa: FBT001
        password = (
            extracted
            if extracted
            else Faker(
                "password",
                length=42,
                special_chars=True,
                digits=True,
                upper_case=True,
                lower_case=True,
            ).evaluate(None, None, extra={"locale": None})
        )
        self.set_password(password)
        if create:
            self.save()

    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True


class ReviewerFactory(UserFactory):
    """An explicitly authorized test reviewer, unlike an unprivileged staff user."""

    is_nha_team = True

    @post_generation
    def portal_access(self, create, extracted, **kwargs):
        from ohc_experience.experiences.models import AccessGrant  # noqa: PLC0415
        from ohc_experience.experiences.registry import registry  # noqa: PLC0415

        if create:
            for program in registry.programs():
                for area in AccessGrant.Area.values:
                    AccessGrant.objects.create(
                        user=self,
                        program=program.key,
                        area=area,
                        category="*",
                        can_read=True,
                        can_write=True,
                        can_approve=True,
                    )
