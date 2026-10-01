from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

loaded = load_dotenv(
    dotenv_path=ENV_FILE,
    override=True,
)

from app.api.onboarding_progress_routes import (
    router as onboarding_progress_router,
)
from app.api.workspace_health_routes import (
    router as workspace_health_router,
)

from app.api.stripe_webhook_routes import (
    router as stripe_webhook_router,
)

logger = logging.getLogger(__name__)


def _env_present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def _log_startup_environment() -> None:
    """
    Log configuration presence without printing secrets.

    Tenant identity must NOT be loaded globally from an environment variable.
    organization_id is request-scoped and must come from the authenticated
    AuthUser in route/service/repository layers.
    """
    logger.info("Python version: %s", sys.version)
    logger.info("Environment file: %s", ENV_FILE)
    logger.info("Environment file exists: %s", ENV_FILE.exists())
    logger.info("Environment loaded: %s", loaded)

    logger.info(
        "GOOGLE_OAUTH_CLIENT_ID present: %s",
        _env_present("GOOGLE_OAUTH_CLIENT_ID"),
    )
    logger.info(
        "GOOGLE_OAUTH_CLIENT_SECRET present: %s",
        _env_present("GOOGLE_OAUTH_CLIENT_SECRET"),
    )
    logger.info(
        "GOOGLE_OAUTH_REDIRECT_URI present: %s",
        _env_present("GOOGLE_OAUTH_REDIRECT_URI"),
    )
    logger.info(
        "GOOGLE_OAUTH_STATE_SECRET present: %s",
        _env_present("GOOGLE_OAUTH_STATE_SECRET"),
    )

    logger.info(
        "STRIPE_SECRET_KEY present: %s",
        _env_present("STRIPE_SECRET_KEY"),
    )
    logger.info(
        "STRIPE_PRICE_ID present: %s",
        _env_present("STRIPE_PRICE_ID"),
    )
    logger.info(
        "STRIPE_SUCCESS_URL present: %s",
        _env_present("STRIPE_SUCCESS_URL"),
    )
    logger.info(
        "STRIPE_CANCEL_URL present: %s",
        _env_present("STRIPE_CANCEL_URL"),
    )


def _require_startup_environment() -> None:
    required = [
        "GOOGLE_CLIENT_ID",
    ]

    missing = [
        name
        for name in required
        if not _env_present(name)
    ]

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )


_log_startup_environment()
_require_startup_environment()


# These imports intentionally remain below load_dotenv().
from app.api.auth import router as auth_router
from app.api.billing_routes import router as billing_router
from app.api.routes import router as api_router


app = FastAPI(
    title="AI Data Steward Copilot API",
    version="1.0.0",
)

app.include_router(
    stripe_webhook_router
)


def _cors_origins() -> list[str]:
    configured = (
        os.getenv("CORS_ALLOWED_ORIGINS")
        or (
            "http://localhost:5173,"
            "http://127.0.0.1:5173,"
            "https://steward-copilot-ui-503305938314.us-east1.run.app,"
            "https://www.admsdata.com,"
            "https://admsdata.com"
        )
    )

    return [
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=["*"],
)


# Tenant-aware application routes.
#
# organization_id is intentionally NOT configured here. It must be resolved
# per authenticated request by get_current_user() and propagated through the
# route -> service -> repository call chain.
app.include_router(api_router, prefix="/v1")

# Authentication routes.
app.include_router(auth_router)

# billing_router already defines prefix="/v1/billing".
app.include_router(billing_router)

# These routers retain their own route prefixes.
app.include_router(onboarding_progress_router)
app.include_router(workspace_health_router)


@app.get("/health")
def health() -> dict[str, str]:
    """
    Public liveness endpoint.

    This endpoint intentionally contains no tenant data and therefore does
    not require organization_id.
    """
    return {"status": "ok"}


@app.get("/routes")
def routes() -> list[str]:
    """
    Development/diagnostic route inventory.

    Consider disabling this endpoint in production if you do not want to
    expose API route metadata publicly.
    """
    return [route.path for route in app.routes]


