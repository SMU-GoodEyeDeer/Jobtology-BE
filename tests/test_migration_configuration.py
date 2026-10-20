import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_URL = "postgresql+asyncpg://migration_probe@127.0.0.1:1/migration_probe"


def _run(arguments, *, database_url=None):
    environment = dict(os.environ)
    environment.pop("JOBTOLOGY_DATABASE_URL", None)
    environment.pop("ALEMBIC_CONFIG", None)
    if database_url is not None:
        environment["JOBTOLOGY_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15, check=False,
    )


def test_missing_database_url_reports_the_required_setting_without_an_ini_file():
    result = _run(["-c", "pyproject.toml", "current"])
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Set JOBTOLOGY_DATABASE_URL or -x database_url=..." in output
    assert "No config file" not in output
    assert "Traceback" not in output


@pytest.mark.parametrize("source", ["environment", "argument", "ini"])
def test_migration_url_sources_work_without_opening_a_database(source, tmp_path):
    arguments = ["-c", "pyproject.toml"]
    database_url = None
    if source == "environment":
        database_url = PROBE_URL
    elif source == "argument":
        arguments += ["-x", f"database_url={PROBE_URL}"]
        # The explicit argument must retain precedence over the environment.
        database_url = "invalid_dialect://unused"
    else:
        config = tmp_path / "migration.ini"
        config.write_text(f"[alembic]\nscript_location = {ROOT / 'migrations'}\n"
                          f"sqlalchemy.url = {PROBE_URL}\n")
        arguments += ["-c", str(config)]
    result = _run([*arguments, "upgrade", "base", "--sql"], database_url=database_url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BEGIN;" in result.stdout
    assert "COMMIT;" in result.stdout
