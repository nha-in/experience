import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


@pytest.mark.parametrize("password", ["sandbox-kerala-2026", "SANDBOX-KERALA-2026"])
def test_accepts_a_letter_of_either_case_with_a_number_and_a_special_character(
    password,
):
    validate_password(password)


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("2026-0917-4471", id="no letter"),
        pytest.param("sandbox-kerala-portal", id="no number"),
        pytest.param("sandboxKerala2026", id="no special character"),
        pytest.param("sandbox Kerala 2026", id="a space is not a special character"),
        pytest.param(
            "MüllerKerala2026",
            id="an accented letter is not a special character",
        ),
    ],
)
def test_rejects_a_password_without_a_letter_a_number_and_a_special_character(
    password,
):
    with pytest.raises(
        ValidationError,
        match="at least one letter, one number and one special character",
    ):
        validate_password(password)
