from __future__ import annotations

import os
import subprocess
import sys


def required_role() -> str:
    return os.getenv(
        "STAY_SERVICE_ROLE",
        "web",
    ).strip().lower()


def main() -> int:
    role = required_role()

    if role == "avatar-worker":
        print(
            "Avatar worker pre-deploy complete: "
            "no database contract is owned by this service."
        )
        return 0

    if role != "web":
        raise RuntimeError(
            f"Unsupported STAY_SERVICE_ROLE: {role}"
        )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_database_migrations.py",
        ],
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode
    # Migration success alone does not establish runtime compatibility.
    readiness = subprocess.run(
        [sys.executable, "-c",
         "from app.services.pgvector_memory_service import PGVectorMemoryService; "
         "PGVectorMemoryService(); print('Canonical memory runtime verified.')"],
        check=False,
    )
    return readiness.returncode


if __name__ == "__main__":
    raise SystemExit(main())
