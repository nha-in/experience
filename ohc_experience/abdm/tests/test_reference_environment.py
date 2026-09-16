# ruff: noqa: F811, PLR2004
import pytest
from django.urls import reverse

from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.reference import ABDMReferenceEnvironment
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def reference_url(workspace):
    return reverse("experiences:reference-environment", args=[workspace.reference])


def main_nav(response):
    html = response.content.decode()
    return html.split('<nav id="app-nav"', 1)[1].split('class="mt-auto', 1)[0]


def test_integrators_get_the_m1_and_m2_command_and_flows(environment, client):
    workspace = environment["workspace"]
    client.force_login(environment["applicant"])

    page = client.get(reference_url(workspace))

    assert page.status_code == 200
    html = page.content.decode()
    assert f'data-copy="{ABDMReferenceEnvironment.run_command}"' in html
    assert "M1 and M2 flows on http://localhost:8000" in html
    assert [milestone.code for milestone, _ in page.context["reference_flows"]] == [
        "M1",
        "M2",
    ]
    assert "Health record request and data transfer" in html
    assert 'alt="Open Healthcare Network"' in html
    assert 'alt="eGov Foundation"' in html
    assert "Digital Public Good · MIT licensed" in html
    link = main_nav(page).split('id="nav-reference"', 1)[1].split(">", 1)[0]
    assert 'aria-current="page"' in link


def test_support_members_have_no_reference_page(environment, client):
    workspace = environment["workspace"]
    support = UserFactory()
    Membership.objects.create(
        organisation=environment["org"],
        user=support,
        role=Role.SUPPORT,
    )
    client.force_login(support)

    assert client.get(reference_url(workspace)).status_code == 403
    overview = client.get(workspace.get_absolute_url())
    assert 'id="nav-reference"' not in main_nav(overview)


def test_other_organisations_cannot_find_the_page(environment, client):
    client.force_login(environment["outsider"])

    assert client.get(reference_url(environment["workspace"])).status_code == 404


def test_a_program_without_a_reference_environment_has_no_page(
    environment,
    client,
    monkeypatch,
):
    monkeypatch.setattr(ABDM, "reference_environment", None)
    workspace = environment["workspace"]
    client.force_login(environment["applicant"])

    assert client.get(reference_url(workspace)).status_code == 404
    overview = client.get(workspace.get_absolute_url())
    assert 'id="nav-reference"' not in main_nav(overview)
