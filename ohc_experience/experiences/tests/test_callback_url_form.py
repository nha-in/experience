import pytest

from ohc_experience.experiences.forms import CallbackURLForm


@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.5/callback",
        "https://[::1]/callback",
        "https://localhost/callback",
        "https://keycloak/callback",
        "https://hiecm.internal/callback",
        "http://integrator.example/callback",
    ],
)
def test_a_callback_url_hie_cm_must_not_call_is_refused(url):
    assert not CallbackURLForm({"callback_url": url}).is_valid()


@pytest.mark.parametrize("url", ["", "https://integrator.example/v3/callback"])
def test_a_public_https_callback_url_is_taken(url):
    assert CallbackURLForm({"callback_url": url}).is_valid()
