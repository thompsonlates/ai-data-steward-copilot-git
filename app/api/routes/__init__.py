"""API router aggregator.

All endpoint paths, response models, dependencies, and status codes are preserved
from the supplied monolithic routes.py. The application can continue importing:

    from app.api.routes import router
"""

from fastapi import APIRouter

from . import onedrive_routes
from . import google_sheets_routes
from . import databricks_routes
from . import csv_routes
from . import oauth_routes
from . import connection_routes
from . import dq_profile_routes
from . import dq_recommendation_routes
from . import dq_remediation_routes
from . import dq_execution_routes
from . import governance_routes
from . import policy_routes
from . import metrics_routes
from . import match_routes
from . import onboarding_routes
from . import user_routes
from . import entitlement_routes
from . import search_routes
from . import auth_routes

from app.api.routes.bigquery_routes import router as bigquery_router


router = APIRouter()

router.include_router(onedrive_routes.router)
router.include_router(google_sheets_routes.router)
router.include_router(databricks_routes.router)
router.include_router(bigquery_router)
router.include_router(csv_routes.router)
router.include_router(oauth_routes.router)
router.include_router(connection_routes.router)
router.include_router(dq_profile_routes.router)
router.include_router(dq_recommendation_routes.router)
router.include_router(dq_remediation_routes.router)
router.include_router(dq_execution_routes.router)
router.include_router(governance_routes.router)
router.include_router(policy_routes.router)
router.include_router(metrics_routes.router)
router.include_router(match_routes.router)
router.include_router(onboarding_routes.router)
router.include_router(user_routes.router)
router.include_router(entitlement_routes.router)
router.include_router(search_routes.router)
router.include_router(auth_routes.router)
