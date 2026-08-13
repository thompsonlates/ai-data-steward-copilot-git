from __future__ import annotations

import os

from dotenv import load_dotenv


def require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


def main() -> None:
    load_dotenv()

    # This test file does not query tenant data directly, so there is no
    # database WHERE organization_id filter to apply here. We still require
    # ORGANIZATION_ID so any downstream Jira test can carry an explicit
    # tenant context.
    organization_id = require_env("ORGANIZATION_ID")

    google_client_id = require_env("GOOGLE_CLIENT_ID")

    print("Tenant context loaded.")
    print(f"ORGANIZATION_ID: {organization_id}")
    print(f"GOOGLE_CLIENT_ID configured: {bool(google_client_id)}")


if __name__ == "__main__":
    main()
