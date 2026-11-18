import pytest

from ohc_experience.legacy_import.management.commands.backfill_pincodes import (
    pincode_in,
)


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
