from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from ohc_experience.abdm import demo
from ohc_experience.abdm import forms
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.organisations.lgd import LGDLookupError
from ohc_experience.organisations.models import Organisation

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
    # The approved M1 comes with a visibly fake production client ID.
    recorded = Product.objects.exclude(production_client_id="").get()
    assert recorded.production_client_id.startswith("DEMO_PROD_SBX_")
    with pytest.raises(CommandError, match="already exists"):
        call_command("seed_experience_demo", stdout=StringIO())


def test_engine_demo_command_is_disabled_in_production(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_experience_demo", stdout=StringIO())


@pytest.mark.parametrize("unavailable", [True, False])
def test_demo_lgd_preflight_preserves_existing_data(settings, monkeypatch, unavailable):
    settings.DEBUG = True
    organisation = Organisation.objects.create(name="Existing organisation")

    def failed_lookup(pincode):
        assert pincode == "560001"
        if unavailable:
            raise LGDLookupError
        return []

    monkeypatch.setattr(demo, "lookup_pincode", failed_lookup)
    with pytest.raises(CommandError, match="No data has been changed"):
        call_command("seed_experience_demo", reset=True, stdout=StringIO())

    assert Organisation.objects.get(pk=organisation.pk).name == "Existing organisation"
    assert not ProductWorkspace.objects.exists()


def test_demo_skip_lgd_seeds_while_lgd_is_unavailable(settings, monkeypatch):
    settings.DEBUG = True
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }

    def unavailable(pincode):
        raise LGDLookupError

    monkeypatch.setattr(demo, "lookup_pincode", unavailable)
    monkeypatch.setattr(forms, "lookup_pincode", unavailable)
    call_command("seed_experience_demo", skip_lgd=True, stdout=StringIO())

    assert ProductWorkspace.objects.exists()
    assert FormSubmission.objects.filter(data__district_lgd_code="525").exists()
    # The stand-in answers only while the seed runs.
    assert forms.lookup_pincode is unavailable


def test_demo_evidence_uses_first_active_abdm_agency():
    CertificationAgency.objects.all().delete()
    CertificationAgency.objects.create(
        program="abdm",
        name="Disabled agency",
        is_active=False,
    )
    CertificationAgency.objects.create(program="other", name="Another program's agency")
    CertificationAgency.objects.create(
        program="abdm",
        name="Alphabetically first agency",
        sort_order=2,
    )
    CertificationAgency.objects.create(
        program="abdm",
        name="Prioritized agency",
        sort_order=1,
    )

    assert demo.evidence_data()["wasa_agency"] == "Prioritized agency"


@pytest.mark.parametrize("reset", [True, False])
def test_demo_without_active_agency_preserves_existing_data(
    settings,
    monkeypatch,
    reset,
):
    settings.DEBUG = True
    organisation = Organisation.objects.create(name="Existing organisation")
    CertificationAgency.objects.filter(program="abdm").update(is_active=False)
    CertificationAgency.objects.create(program="other", name="Other program agency")
    agencies_before = list(CertificationAgency.objects.order_by("pk").values())

    def unexpected_reset(*args, **kwargs):
        pytest.fail("The demo must check for an active agency before resetting data.")

    monkeypatch.setattr(demo, "call_command", unexpected_reset)
    with pytest.raises(CommandError, match="No active ABDM certification agency"):
        call_command("seed_experience_demo", reset=reset, stdout=StringIO())

    assert Organisation.objects.get(pk=organisation.pk).name == "Existing organisation"
    assert list(CertificationAgency.objects.order_by("pk").values()) == agencies_before
    assert not ProductWorkspace.objects.exists()


def test_demo_reset_preserves_agency_master_data_and_sequence(settings):
    settings.DEBUG = True
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    organisation = Organisation.objects.create(name="Organisation before reset")
    agency = CertificationAgency.objects.filter(program="abdm").first()
    agency.name = "Administrator's renamed agency"
    agency.is_active = False
    agency.sort_order = 900
    agency.save()
    CertificationAgency.objects.create(
        program="abdm",
        name="Administrator's custom agency",
        sort_order=800,
    )
    CertificationAgency.objects.create(
        pk=10000,
        program="other",
        name="Another program's inactive agency",
        is_active=False,
        sort_order=700,
    )
    earlier = timezone.now() - timedelta(days=60)
    CertificationAgency.objects.update(created_at=earlier, updated_at=earlier)
    agencies_before = list(CertificationAgency.objects.order_by("pk").values())

    call_command("seed_experience_demo", reset=True, stdout=StringIO())

    assert not Organisation.objects.filter(name=organisation.name).exists()
    assert ProductWorkspace.objects.filter(experience_type="abdm").exists()
    assert list(CertificationAgency.objects.order_by("pk").values()) == agencies_before
    new_agency = CertificationAgency.objects.create(
        program="abdm",
        name="Agency added after reset",
    )
    assert new_agency.pk > max(agency["id"] for agency in agencies_before)
