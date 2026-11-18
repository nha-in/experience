import pytest

from ohc_experience.legacy_import.management.commands.backfill_pincodes import (
    pincode_in,
)
from ohc_experience.legacy_import.management.commands.backfill_pincodes import repairs

#: One PIN another organisation filed with a single state and district.
PIN_PAIRS = {"560038": ("Karnataka", "Bengaluru Urban")}


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("Anusandhan Bhawan, C-56/1, sector 62, Noida 201307", "201307"),
        ("Royal Barter, Residency Road, Bangalore KA 560025 IN", "560025"),
        ("CIE, IIIT-H, Gachibowli, Hyderabad, PIN - 500032", "500032"),
        ("2LNHO Compound, K M Munshi Road, Mumbai - 400007", "400007"),
        ("Unit 110092, Rohini, New Delhi 110011", "110011"),
        ("Phone 9880012345, Bengaluru", ""),
        ("GSTIN 29AABCU9603R1ZJ", ""),
        ("Sector 62, Noida 012345", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_the_pin_an_address_ends_with(address, expected):
    assert pincode_in(address) == expected


def test_the_pin_the_address_ends_with_is_filled():
    answers = {"registered_address": "CMH Road, Indiranagar, Bangalore 560038"}

    assert repairs(answers, {}) == {"pincode": "560038"}


def test_legacys_spelling_is_put_in_the_words_of_the_form():
    answers = {"pincode": "682001", "state": "KERALA", "district": "ERNAKULAM"}

    assert repairs(answers, {}) == {"state": "Kerala", "district": "Ernakulam"}


def test_a_state_and_district_come_from_another_organisation_on_that_pin():
    assert repairs({"pincode": "560038"}, PIN_PAIRS) == {
        "state": "Karnataka",
        "district": "Bengaluru Urban",
    }


def test_a_district_the_list_does_not_know_is_left_as_it_is():
    answers = {"pincode": "560038", "state": "KARNATAKA", "district": "BANGALORE"}

    assert repairs(answers, PIN_PAIRS) == {}


def test_answers_the_form_would_accept_are_left_alone():
    answers = {
        "pincode": "682001",
        "state": "Kerala",
        "district": "Ernakulam",
        "registered_address": "Marine Drive, Kochi 682001",
    }

    assert repairs(answers, PIN_PAIRS) == {}
