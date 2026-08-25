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
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
