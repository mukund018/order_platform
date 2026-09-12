"""Repo-wide check: every service builds its app from the environment .env.example documents.

The per-service suites set their own environment, so they cannot catch a setting that was
renamed in config.py but never added to .env.example, or a router that stopped being
included. Both of those present as a container that crash-loops on deploy, which is an
expensive way to find out.

Nothing connects here - SQLAlchemy engines and redis clients are built lazily - so the
compose hostnames not resolving is fine and deliberate.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

EXPECTED_PATHS = {
    "inventory": {
        "/health",
        "/ready",
        "/products",
        "/products/{sku}",
        "/products/{sku}/stock",
        "/reservations",
        "/reservations/{order_id}/commit",
        "/reservations/{order_id}/release",
    },
    "payments": {"/health", "/ready", "/payments", "/payments/{order_id}"},
    "orders": {
        "/health",
        "/ready",
        "/orders",
        "/orders/{order_id}",
        "/orders/{order_id}/cancel",
        "/reports/daily",
    },
}

PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
import app.main as main

paths = sorted(main.app.openapi()["paths"])
with TestClient(main.app) as client:
    metrics_status = client.get("/metrics").status_code
print("PROBE " + json.dumps({"paths": paths, "metrics_status": metrics_status}))
"""


def _env_from_example() -> dict[str, str]:
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": os.environ.get("PATH", "")}
    for raw in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    env["LOG_DIR"] = ""
    return env


@pytest.mark.parametrize("service", sorted(EXPECTED_PATHS))
def test_service_builds_from_the_documented_environment(service: str) -> None:
    service_dir = ROOT / "services" / service
    if not (service_dir / "app" / "main.py").exists():
        pytest.skip(f"{service} has no main.py yet")

    result = subprocess.run(
        [str(PYTHON), "-c", PROBE, str(service_dir)],
        cwd=service_dir,
        env=_env_from_example(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{service} failed to build:\n{result.stderr[-2000:]}"

    probe = next(line for line in result.stdout.splitlines() if line.startswith("PROBE "))
    info = json.loads(probe[len("PROBE ") :])

    missing = EXPECTED_PATHS[service] - set(info["paths"])
    assert not missing, f"{service} is not serving {sorted(missing)}"

    # /metrics is include_in_schema=False, so it never shows up in the openapi paths.
    assert info["metrics_status"] == 200, f"{service} /metrics returned {info['metrics_status']}"
