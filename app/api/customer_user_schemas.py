from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr


class CustomerUserInviteRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    organization_role: str = "STEWARD"
    billing_role: str = "MEMBER"


class CustomerUserResponse(BaseModel):
    customer_user_id: str
    customer_id: str
    organization_id: str
    user_id: str | None = None
    email: str
    display_name: str | None = None
    organization_role: str
    billing_role: str
    invitation_status: str
    invited_at: datetime | None = None
    accepted_at: datetime | None = None
    is_active: bool
    created_at: datetime
    created_by: str | None = None
    updated_at: datetime | None = None