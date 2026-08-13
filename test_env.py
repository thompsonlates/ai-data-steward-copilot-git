from __future__ import annotations

import os

from dotenv import load_dotenv


def require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value


def main() -> None:
    load_dotenv()

    # Tenant context is required for environment checks that may be used
    # by downstream multi-tenant services.
    organization_id = require_env("ORGANIZATION_ID")

    # Do not print the actual client ID or other sensitive values.
    google_client_id = require_env("GOOGLE_CLIENT_ID")

    print("Environment validation succeeded.")
    print(f"ORGANIZATION_ID: {organization_id}")
    print(f"GOOGLE_CLIENT_ID configured: {bool(google_client_id)}")


if __name__ == "__main__":
    main()
