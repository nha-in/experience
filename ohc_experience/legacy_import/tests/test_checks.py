from ohc_experience.legacy_import import clean
from ohc_experience.legacy_import.checks import target_drift


def test_the_import_tables_still_match_the_portal():
    assert target_drift() == []


def test_drift_names_every_table_the_portal_has_moved_past(monkeypatch):
    monkeypatch.setattr(clean, "SOLUTION_ORDER", [*clean.SOLUTION_ORDER, "payers"])
    monkeypatch.setattr(clean, "ENTITY_TYPES", {**clean.ENTITY_TYPES, "ngo": "charity"})
    monkeypatch.setattr(
        clean,
        "MILESTONE_ORDER",
        [key for key in clean.MILESTONE_ORDER if key != "m2"],
    )

    assert target_drift() == [
        "solution type 'payers' is no longer offered",
        "entity type 'charity' is no longer offered",
        "milestone 'm2' is missing from the import's order",
    ]
