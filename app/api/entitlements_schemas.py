from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DomainEntitlementResponse(BaseModel):
    organization_id: str
    domain: str
    is_enabled: bool
    plan_code: str
    entitlement_source: str

    effective_from: datetime | None = None
    effective_to: datetime | None = None

    created_by: str | None = None
    updated_by: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None