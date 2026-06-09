from datetime import datetime, timedelta, timezone
import os

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from jose import jwt
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
APP_JWT_SECRET = os.getenv("APP_JWT_SECRET")
APP_JWT_ALGORITHM = os.getenv("APP_JWT_ALGORITHM", "HS256")
APP_JWT_EXPIRE_MINUTES = int(os.getenv("APP_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID is not configured")

if not APP_JWT_SECRET:
    raise RuntimeError("APP_JWT_SECRET is not configured")


class GoogleAuthRequest(BaseModel):
    credential: str


class AuthUser(BaseModel):
    email: EmailStr
    name: str | None = None
    picture: str | None = None
    google_sub: str
    email_verified: bool = False


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUser


def create_app_jwt(user: AuthUser) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.google_sub,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "email_verified": user.email_verified,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=APP_JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_JWT_SECRET, algorithm=APP_JWT_ALGORITHM)


@router.post("/google", response_model=AuthResponse)
def google_login(payload: GoogleAuthRequest):
    try:
        info = id_token.verify_oauth2_token(
         payload.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
)

    except Exception as e:
        print("Google token verification failed:", repr(e))
        raise HTTPException(status_code=401, detail=f"Invalid Google credential: {e}")

    email = info.get("email")
    sub = info.get("sub")
    email_verified = bool(info.get("email_verified", False))

    if not email or not sub:
        raise HTTPException(
            status_code=401,
            detail="Missing required Google identity fields",
        )

    user = AuthUser(
        email=email,
        name=info.get("name"),
        picture=info.get("picture"),
        google_sub=sub,
        email_verified=email_verified,
    )

    app_token = create_app_jwt(user)

    return AuthResponse(
        access_token=app_token,
        expires_in=APP_JWT_EXPIRE_MINUTES * 60,
        user=user,
    )

