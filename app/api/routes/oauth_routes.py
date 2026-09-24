"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post("/oauth/microsoft/start")
def start_microsoft_oauth(
    request: OneDriveOAuthStartRequest,
    current_user: AuthUser = Depends(
        get_current_user
    ),
):
    result = (
        onedrive_oauth_service
        .start_authorization(
            connection_id=request.connection_id,
            organization_id=(
                require_current_organization_id(
                    current_user
                )
            ),
            current_user_email=str(
                current_user.email
            ),
            return_url=request.return_url,
        )
    )

    return {
        "authorization_url": result.authorization_url,
        "connection_id": result.connection_id,
    }


@router.get("/oauth/microsoft/callback")
def microsoft_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    try:
        result = (
            onedrive_oauth_service
            .complete_authorization(
                code=code,
                state_token=state,
                oauth_error=error,
                oauth_error_description=(
                    error_description
                ),
            )
        )

        return RedirectResponse(
            url=result.return_url,
            status_code=status.HTTP_302_FOUND,
        )

    except HTTPException as exc:
        logger.warning(
            "Microsoft OAuth callback failed "
            "with HTTP error: %s",
            exc.detail,
        )

        failure_url = (
            onedrive_oauth_service
            .build_failure_return_url(
                state_token=state,
                message=str(exc.detail),
            )
        )

        return RedirectResponse(
            url=failure_url,
            status_code=status.HTTP_302_FOUND,
        )

    except Exception:
        logger.exception(
            "Unexpected Microsoft OAuth "
            "callback failure."
        )

        failure_url = (
            onedrive_oauth_service
            .build_failure_return_url(
                state_token=state,
                message=(
                    "Microsoft authorization completed, "
                    "but the connection could not be "
                    "activated."
                ),
            )
        )

        return RedirectResponse(
            url=failure_url,
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/oauth/google/start")
def start_google_oauth(
    request: GoogleOAuthStartRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    result = google_oauth_service.start_authorization(
        connection_id=request.connection_id,
        organization_id=require_current_organization_id(current_user),
        current_user_email=current_user.email,
        project_id=request.project_id,
        return_url=request.return_url,
    )

    return {
        "authorization_url": result.authorization_url,
        "connection_id": result.connection_id,
    }


@router.get("/oauth/google/callback")
def google_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    try:
        logger.info(
            "Google OAuth callback received. "
            "code_present=%s state_present=%s error=%s",
            bool(code),
            bool(state),
            error,
        )

        result = google_oauth_service.complete_authorization(
            code=code,
            state_token=state,
            oauth_error=error,
            oauth_error_description=error_description,
        )

        logger.info(
            "Google OAuth completed successfully. connection_id=%s",
            result.connection_id,
        )

        return RedirectResponse(
            url=result.return_url,
            status_code=status.HTTP_302_FOUND,
        )

    except HTTPException as exc:
        logger.warning(
            "Google OAuth callback failed with HTTP error: %s",
            exc.detail,
        )

        failure_url = google_oauth_service.build_failure_return_url(
            state_token=state,
            message=str(exc.detail),
        )

        return RedirectResponse(
            url=failure_url,
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as exc:
        logger.exception(
            "Unexpected Google OAuth callback failure."
        )

        failure_url = google_oauth_service.build_failure_return_url(
            state_token=state,
            message=(
                "Google authorization completed, but the connection "
                "could not be activated."
            ),
        )

        return RedirectResponse(
            url=failure_url,
            status_code=status.HTTP_302_FOUND,
        )
