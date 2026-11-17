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


@pytest.mark.parametrize(
    "url",
    [
        "https://integrator.example:8443/callback",
        "https://integrator.example/Callback",
        "https://integrator.example/abha_callback",
        "https://integrator.example/callback.php",
        "https://integrator.example/callback:9000",
        "https://integrator.example/callback?retry=1",
        "https://integrator.example/call back",
    ],
)
def test_a_callback_url_the_gateway_refuses_is_refused_here(url):
    assert not CallbackURLForm({"callback_url": url}).is_valid()


def test_a_trailing_slash_is_dropped_rather_than_refused():
    form = CallbackURLForm({"callback_url": "https://integrator.example/callback/"})

    assert form.is_valid()
    assert form.cleaned_data["callback_url"] == "https://integrator.example/callback"
