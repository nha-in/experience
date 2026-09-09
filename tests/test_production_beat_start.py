import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("migration_status", "beat_status"),
    [(0, 0), (1, 0), (0, 7)],
)
def test_beat_runs_only_after_successful_migrations(
    tmp_path,
    migration_status,
    beat_status,
):
    for name, status in (("python", migration_status), ("celery", beat_status)):
        executable = tmp_path / name
        executable.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{name}' \"$@\"\nexit {status}\n",
        )
        executable.chmod(0o700)
    start = (
        Path(__file__).resolve().parents[1]
        / "compose/production/django/celery/beat/start"
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", str(start)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.defpath}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    expected = ["python", "/app/manage.py", "migrate", "--noinput"]
    if not migration_status:
        expected.extend(["celery", "-A", "config.celery_app", "beat", "-l", "INFO"])
    assert result.stdout.splitlines() == expected
    assert result.returncode == (migration_status or beat_status)
