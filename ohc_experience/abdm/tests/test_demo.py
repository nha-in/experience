from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.models import ReviewItem

pytestmark = pytest.mark.django_db


def test_engine_demo_command_runs_registered_abdm_builder(settings):
    settings.DEBUG = True
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    call_command("seed_experience_demo", stdout=StringIO())
    assert ProductWorkspace.objects.filter(experience_type="abdm").exists()
    assert ProductCredential.objects.filter(status="active").exists()
    assert ReviewItem.objects.filter(status="query_raised").exists()
    with pytest.raises(CommandError, match="already exists"):
        call_command("seed_experience_demo", stdout=StringIO())


def test_engine_demo_command_is_disabled_in_production(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_experience_demo", stdout=StringIO())
