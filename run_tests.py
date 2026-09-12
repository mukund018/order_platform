#!/usr/bin/env python
"""Run every test suite in the repo and summarise the results.

The three services each install a top-level package called `app`, so they cannot share a
single pytest process - whichever one imports first wins and the others get the wrong
models back. Each suite therefore runs in its own subprocess from its own directory,
which is also how they run in CI and in the containers.

    python run_tests.py                 everything
    python run_tests.py orders common   only those
    python run_tests.py --cov           with coverage
    python run_tests.py -- -k reserve   anything after -- goes to pytest
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = ROOT / ".venv" / "bin" / "python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

SUITES = {
    "common": ROOT / "common",
    "inventory": ROOT / "services" / "inventory",
    "payments": ROOT / "services" / "payments",
    "orders": ROOT / "services" / "orders",
    "tools": ROOT,
    "repo": ROOT,
}

# tools/ and the repo-level checks have no pyproject of their own, so they need the path.
EXTRA_PATHS = {"tools": ["tools/tests"], "repo": ["tests"]}

COUNT = re.compile(r"(\d+) (passed|failed|skipped|error)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(name: str, cov: bool, passthrough: list[str]) -> tuple[int, dict[str, int]]:
    # No -q here. The service pyprojects already set it in addopts, and a second one
    # means -qq, which drops the summary line this script reads.
    command = [str(PYTHON), "-m", "pytest", *EXTRA_PATHS.get(name, [])]
    if cov:
        command += ["--cov", "--cov-report=term:skip-covered"]
    command += passthrough

    result = subprocess.run(command, cwd=SUITES[name], capture_output=True, text=True)
    output = ANSI.sub("", result.stdout + result.stderr)
    counts = {word: int(n) for n, word in COUNT.findall(output)}

    if result.returncode not in (0, 5):  # 5 is "no tests collected"
        print(f"\n----- {name} -----")
        print(output.strip()[-4000:])
    if cov:
        for line in output.splitlines():
            if line.startswith("TOTAL"):
                counts["coverage"] = int(line.split()[-1].rstrip("%"))

    return result.returncode, counts


def main() -> int:
    argv = sys.argv[1:]
    passthrough: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, passthrough = argv[:split], argv[split + 1 :]

    cov = "--cov" in argv
    wanted = [a for a in argv if not a.startswith("-")] or list(SUITES)
    unknown = [name for name in wanted if name not in SUITES]
    if unknown:
        print(f"unknown suite(s): {', '.join(unknown)}\nknown: {', '.join(SUITES)}")
        return 2

    totals: dict[str, int] = {}
    failed_suites: list[str] = []

    header = f"{'suite':<12} {'passed':>7} {'failed':>7} {'skipped':>8}"
    print(header + (f" {'cov':>5}" if cov else ""))
    print("-" * len(header + ("      " if cov else "")))

    for name in wanted:
        code, counts = run(name, cov, passthrough)
        for key in ("passed", "failed", "skipped"):
            totals[key] = totals.get(key, 0) + counts.get(key, 0)
        if code not in (0, 5):
            failed_suites.append(name)

        row = (
            f"{name:<12} {counts.get('passed', 0):>7} "
            f"{counts.get('failed', 0) + counts.get('error', 0):>7} "
            f"{counts.get('skipped', 0):>8}"
        )
        if cov:
            row += f" {counts.get('coverage', 0):>4}%"
        print(row)

    print("-" * len(header + ("      " if cov else "")))
    print(
        f"{'total':<12} {totals.get('passed', 0):>7} "
        f"{totals.get('failed', 0):>7} {totals.get('skipped', 0):>8}"
    )

    if totals.get("skipped"):
        print("\nSkips are the postgres-only concurrency tests. Set TEST_DATABASE_URL to run them.")
    if failed_suites:
        print(f"\nFAILED: {', '.join(failed_suites)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
