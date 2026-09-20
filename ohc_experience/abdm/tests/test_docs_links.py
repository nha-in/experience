"""Every documentation link follows the site the deployment is pointed at.

`ABDM_DOCS_URL` is the only place the documentation host is written down, so
a staging portal sends people to staging rather than to production.
"""

import pytest
from django.urls import reverse

from ohc_experience.abdm.catalog import MILESTONES
from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.forms import ProductRegistrationForm

STAGING = "https://docs.staging.example"


def test_the_catalog_reads_its_links_from_the_deployment(settings):
    # Set with a trailing slash, which must not double up against the path.
    settings.ABDM_DOCS_URL = f"{STAGING}/"

    assert ABDM.docs_site == STAGING
    assert ABDM.docs_url == f"{STAGING}/docs/hiecm/v3"
    assert ABDM.milestones_docs_url == f"{STAGING}/docs/hiecm/v3/milestones"
    assert MILESTONES["m1"].docs_url == f"{STAGING}/docs/hiecm/v3/milestones/m1"
    assert TRACK_MAP["UHI"].docs_url == f"{STAGING}/docs/uhi/v1"
    _, url = ProductRegistrationForm.solution_type_details["phr"]
    assert url == f"{STAGING}/docs/hiecm/v3/concepts/phr"


@pytest.mark.django_db
def test_the_marketing_page_links_to_the_site_the_deployment_names(client, settings):
    settings.ABDM_DOCS_URL = STAGING

    page = client.get(reverse("home"))

    assert f"{STAGING}/docs/hiecm/v3/milestones/m1".encode() in page.content
    assert f"{STAGING}/agent-setup/prompt.md".encode() in page.content
