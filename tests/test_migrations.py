"""Repo-wide check: every service's migrations must build the schema its models declare.

Nothing else catches the drift. The test suites create their schema with
Base.metadata.create_all, so a model can gain a column and every test still passes while
alembic upgrade head quietly produces a different table - and the first you hear of it is
a container that will not start, or an UndefinedColumn in production.

Each service is migrated into its own throwaway SQLite file, which is why the models are
kept dialect-portable.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

SERVICES = [
    ("inventory", "INVENTORY_DATABASE_URL"),
    ("payments", "PAYMENTS_DATABASE_URL"),
    ("orders", "ORDERS_DATABASE_URL"),
]


def _migrated_schema(service_dir: Path, env_var: str, url: str) -> dict[str, set[str]]:
    env = {**os.environ, env_var: url}
    env.pop("LOG_DIR", None)
    result = subprocess.run(
        [str(PYTHON), "-m", "alembic", "upgrade", "head"],
        cwd=service_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr[-2000:]}"

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        return {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in inspector.get_table_names()
            if table != "alembic_version"
        }
    finally:
        engine.dispose()


def _declared_schema(service_dir: Path, env_var: str, url: str) -> dict[str, set[str]]:
    os.environ[env_var] = url
    sys.path.insert(0, str(service_dir))
    # app.config caches its settings, and a sibling service may already have imported a
    # different app package into sys.modules.
    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    try:
        models = importlib.import_module("app.models")
        return {
            table.name: {column.name for column in table.columns}
            for table in models.Base.metadata.tables.values()
        }
    finally:
        sys.path.remove(str(service_dir))


@pytest.mark.parametrize(("service", "env_var"), SERVICES)
def test_migrations_produce_the_schema_the_models_declare(
    service: str, env_var: str, tmp_path: Path
) -> None:
    service_dir = ROOT / "services" / service
    if not (service_dir / "alembic.ini").exists():
        pytest.skip(f"{service} has no migrations yet")

    url = f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}"
    migrated = _migrated_schema(service_dir, env_var, url)
    declared = _declared_schema(service_dir, env_var, url)

    assert set(migrated) == set(declared)
    for table in sorted(declared):
        assert migrated[table] == declared[table], f"{service}.{table} columns differ"
