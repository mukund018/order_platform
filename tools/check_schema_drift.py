"""INC-003 and INC-010 were both the same underlying gap: the model and the checked-in
migration were correct, and something dropped an index or a constraint directly from a
live database anyway. Neither incident's root cause could have been found by reading a
diff - only by comparing the live schema against what the models say it should be.

Alembic already does exactly that comparison (it's the same autogenerate diffing that
`alembic revision --autogenerate` uses) via `alembic check`: it exits non-zero and lists
what's missing if the live database disagrees with the models. This wraps that command
for all three services that own a schema, from the host, against the running containers.

    python tools/check_schema_drift.py
"""

from __future__ import annotations

import subprocess
import sys

SERVICES = ["orders", "inventory", "payments"]


def main() -> int:
    drifted = []
    for service in SERVICES:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", service, "alembic", "check"],
            capture_output=True,
            text=True,
        )
        clean = result.returncode == 0 and "No new upgrade operations detected" in result.stdout
        print(f"{service}: {'clean' if clean else 'DRIFTED'}")
        if not clean:
            drifted.append(service)
            print(result.stdout.strip())
            print(result.stderr.strip())

    if drifted:
        print(f"\nschema drift detected in: {', '.join(drifted)}")
        return 1

    print("\nno schema drift detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
