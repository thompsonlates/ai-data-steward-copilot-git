from dotenv import load_dotenv
from pathlib import Path
import os


# Load environment variables
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID is not set")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.auth import router as auth_router

import sys

print("PYTHON VERSION:", sys.version)

# FastAPI app
app = FastAPI(
    title="AI Data Steward Copilot API",
    version="1.0.0"
)


# Allowed frontend origins
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://steward-copilot-ui-503305938314.us-east1.run.app",
    "https://www.admsdata.com",
    "https://admsdata.com",
]

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# API routes
app.include_router(router, prefix="/v1")
app.include_router(auth_router)


# Health endpoint
@app.get("/health")
def health():
    return {"status": "ok"}

# Debug route listing
@app.get("/routes")
def routes():
    return [r.path for r in app.routes]