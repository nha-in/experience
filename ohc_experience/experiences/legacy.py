"""What an imported row could not be read from legacy, and who asks about it.

The legacy import leaves a gap on the row it imported when the old sandbox never
recorded an answer the portal needs. A product keeps them in its metadata, an
organisation in its verification form record, so every screen asks the same way.
"""

LEGACY_GAPS = "legacy_gaps"

#: Legacy never recorded a solution type the portal's list offers.
SOLUTION_TYPE = "solution_type"
#: Legacy's NHCX had no payer, provider or patient app role.
NHCX_ROLE = "nhcx_role"
#: The registration declared both HIE-CM and PHR, which cannot share a product.
TRACKS = "tracks"

#: The Other box takes words, and legacy asked for a solution type only from
#: 2024. Saying so beats an empty box the integrator cannot get past.
UNRECORDED_SOLUTION_TYPE = "Not recorded in the legacy sandbox."
#: A registration holding the legacy form's whole list of options. Naming the
#: reason keeps it apart from a registration that was never asked at all.
EVERY_OPTION_SOLUTION_TYPE = (
    "Not recorded in the legacy sandbox, which saved every option on the form."
)
#: Neither is an answer. The integrator writing over one is.
IMPORTED_SOLUTION_NOTES = (UNRECORDED_SOLUTION_TYPE, EVERY_OPTION_SOLUTION_TYPE)

#: What each gap asks the integrator to confirm, in the words its screens show.
GAP_NOTICES = {
    SOLUTION_TYPE: (
        "Your legacy registration recorded no solution type, so this product is "
        "Other. Pick the one that matches it."
    ),
    TRACKS: (
        "Your legacy registration applied for both HIE-CM and PHR. Pick one "
        "solution type to narrow it."
    ),
    NHCX_ROLE: (
        "Your legacy NHCX application named no role, so it reads as Provider. "
        "Pick the one that matches."
    ),
}


def gaps(row):
    """Everything the import could not read for this product or form record."""
    return set((row.metadata or {}).get(LEGACY_GAPS, ()))


def has_gap(row, name):
    """Whether this row carries that one gap."""
    return name in gaps(row)


def notices(row):
    """The lines a screen shows for the gaps this row still carries."""
    held = gaps(row)
    return [text for name, text in GAP_NOTICES.items() if name in held]


def forget_gaps(row, names):
    """Drop the gaps this row has since answered, and save when any were there."""
    held = gaps(row)
    kept = held - set(names)
    if kept == held:
        return
    metadata = row.metadata or {}
    if kept:
        metadata[LEGACY_GAPS] = sorted(kept)
    else:
        metadata.pop(LEGACY_GAPS, None)
    row.metadata = metadata
    row.save(update_fields=["metadata"])
