from ohc_experience.legacy_import.management.commands.import_legacy import upload_report


def test_the_run_keeps_its_csvs_in_storage(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "storage")
    report = tmp_path / "report"
    report.mkdir()
    (report / "counts.csv").write_text("measure,count\norganisations,2\n")
    (report / "review-merged-organisations.csv").write_text("sd_ids,linked_by\n")
    (report / "notes.txt").write_text("not a report\n")

    saved = upload_report(report, "legacy_data/2026-09-29-1800")

    assert saved == [
        "legacy_data/2026-09-29-1800/counts.csv",
        "legacy_data/2026-09-29-1800/review-merged-organisations.csv",
    ]
    stored = tmp_path / "storage" / "legacy_data" / "2026-09-29-1800" / "counts.csv"
    assert stored.read_text() == "measure,count\norganisations,2\n"
