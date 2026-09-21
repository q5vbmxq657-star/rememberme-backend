from __future__ import annotations

import json
import os
import re
import sys


def required_role() -> str:
    return os.getenv(
        "STAY_SERVICE_ROLE",
        "web",
    ).strip().lower()


def main() -> None:
    role = required_role()

    if role not in {"web", "avatar-worker"}:
        raise RuntimeError("Unsupported STAY_SERVICE_ROLE")

    # Only allow infrastructure identifiers; never dump configuration or secrets.
    revision = os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip()
    deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip()
    print(
        json.dumps({
            "event": "stay_service_start",
            "role": role,
            "source_revision": revision.lower()
            if re.fullmatch(r"[0-9a-fA-F]{40}", revision) else None,
            "deployment_id": deployment.lower()
            if re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                deployment,
            ) else None,
        }),
        flush=True,
    )

    if role == "web":
        port = os.getenv("PORT", "8000").strip()
        os.execvp(
            sys.executable,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                port,
                "--proxy-headers",
                "--forwarded-allow-ips=*",
            ],
        )

    if role == "avatar-worker":
        os.execvp(
            sys.executable,
            [
                sys.executable,
                "-m",
                "app.workers.avatar_tavus_worker",
                "start",
            ],
        )

if __name__ == "__main__":
    main()
