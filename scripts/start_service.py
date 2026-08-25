from __future__ import annotations

import os
import sys


def required_role() -> str:
    return os.getenv(
        "STAY_SERVICE_ROLE",
        "web",
    ).strip().lower()


def main() -> None:
    role = required_role()

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

    raise RuntimeError(
        f"Unsupported STAY_SERVICE_ROLE: {role}"
    )


if __name__ == "__main__":
    main()
