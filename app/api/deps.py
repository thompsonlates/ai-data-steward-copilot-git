from __future__ import annotations

"""
Shared FastAPI authentication dependencies.

This module intentionally delegates authentication and tenant resolution to
app.api.auth so the application has one source of truth for JWT validation,
organization_id, customer_id, and user_id.

Do not decode application JWTs independently in this module. Doing so would
risk creating a second authentication path that does not enforce tenant
membership consistently.
"""

from fastapi import Depends

from app.api.auth import (
    AuthUser,
    get_current_tenant_user as _get_current_tenant_user,
    get_current_user as _get_current_user,
)


def get_current_user(
    current_user: AuthUser = Depends(_get_current_user),
) -> AuthUser:
    """
    Return the authenticated user.

    This dependency is appropriate for authentication-only flows such as
    onboarding, where an organization may not exist yet.
    """
    return current_user


def get_current_tenant_user(
    current_user: AuthUser = Depends(_get_current_tenant_user),
) -> AuthUser:
    """
    Return an authenticated user with verified tenant membership.

    Use this dependency for all endpoints that read or mutate
    organization-scoped customer data.
    """
    return current_user


def require_organization_id(
    current_user: AuthUser = Depends(_get_current_tenant_user),
) -> str:
    """
    Convenience dependency that returns the authenticated organization_id.

    The underlying auth dependency already fails closed if tenant membership
    is missing, so this value is safe to propagate into service/repository
    queries as @organization_id.
    """
    organization_id = current_user.organization_id

    if organization_id is None:
        # Defensive only: _get_current_tenant_user should already prevent this.
        raise RuntimeError(
            "Tenant authentication succeeded without organization_id."
        )

    return organization_id


def require_customer_id(
    current_user: AuthUser = Depends(_get_current_tenant_user),
) -> str:
    """
    Convenience dependency that returns the authenticated customer_id.
    """
    customer_id = current_user.customer_id

    if customer_id is None:
        # Defensive only: _get_current_tenant_user should already prevent this.
        raise RuntimeError(
            "Tenant authentication succeeded without customer_id."
        )

    return customer_id
