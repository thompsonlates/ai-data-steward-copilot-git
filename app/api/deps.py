import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Header, HTTPException
from jose import JWTError, jwt

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

APP_JWT_SECRET = os.getenv("APP_JWT_SECRET")
APP_JWT_ALGORITHM = os.getenv("APP_JWT_ALGORITHM", "HS256")

if not APP_JWT_SECRET:
    raise RuntimeError("APP_JWT_SECRET is not configured")


def get_current_user(authorization: str | None = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format",
        )

    token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        payload = jwt.decode(token, APP_JWT_SECRET, algorithms=[APP_JWT_ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid or expired token: {str(e)}",
        )