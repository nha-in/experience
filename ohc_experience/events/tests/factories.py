from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from factory import LazyFunction
from factory import Sequence
from factory import Trait
from factory.django import DjangoModelFactory

from ohc_experience.events.models import Event


class EventFactory(DjangoModelFactory[Event]):
    """An event a week out. Unpublished by default — publishing is the choice."""

    title = Sequence(lambda n: f"Partner office hours {n}")
    kind = Event.Kind.OFFICE_HOURS
    summary = "Bring your integration questions."
    description = "Open floor with the OHC integration team."
    starts_at = LazyFunction(lambda: timezone.now() + timedelta(days=7))

    class Meta:
        model = Event

    class Params:
        # EventFactory(published=True) — visible to integrators.
        published = Trait(published_at=LazyFunction(timezone.now))
        # EventFactory(past=True) — already over. ends_at stays null, so the
        # model falls back to starts_at and the check constraint is moot.
        past = Trait(starts_at=LazyFunction(lambda: timezone.now() - timedelta(days=8)))
        # EventFactory(in_person=True) — has a location, so not online.
        in_person = Trait(location="OHC Bengaluru office")
