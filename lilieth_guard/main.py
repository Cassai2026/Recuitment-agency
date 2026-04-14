"""
Lilieth Orchestrator — FastAPI Application Entry Point
======================================================
Provides the REST API for the Dual-Business Hub, bridging:
  - PostgreSQL (dual-business DB)
  - Redis (zero-lag candidate matching cache)
  - Gemini Bridge (local LLM node)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers import whatsapp


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # pragma: no cover
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    app.state.redis = await aioredis.from_url(redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Lilieth Orchestrator",
    description="Dual-Business Hub API — Recruitment & Property Pressure",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Restrict allowed origins via environment variable in production.
    # Default to empty list (no cross-origin requests) for safety.
    allow_origins=os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if os.getenv("CORS_ALLOWED_ORIGINS") else [],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    """Liveness probe used by Docker and Nginx."""
    return JSONResponse({"status": "ok", "service": "lilieth-orchestrator"})


@app.get("/api/v1/info", tags=["ops"])
async def info() -> JSONResponse:
    """Service information endpoint."""
    return JSONResponse({
        "name": "Lilieth Orchestrator",
        "modules": ["recruitment", "property_pressure"],
        "version": "1.0.0",
    })


# ---------------------------------------------------------------------------
# Module 1 — The Intake: WhatsApp webhook
# ---------------------------------------------------------------------------

app.include_router(whatsapp.router)


# ---------------------------------------------------------------------------
# Placeholder routers — extend with your domain logic
# ---------------------------------------------------------------------------

# from lilieth_guard.routers import candidates, jobs, placements
# from lilieth_guard.routers import leads, residential_jobs, commercial_contracts
# from lilieth_guard.routers import rams
#
# app.include_router(candidates.router, prefix="/api/v1")
# app.include_router(jobs.router, prefix="/api/v1")
# app.include_router(placements.router, prefix="/api/v1")
# app.include_router(leads.router, prefix="/api/v1")
# app.include_router(residential_jobs.router, prefix="/api/v1")
# app.include_router(commercial_contracts.router, prefix="/api/v1")
# app.include_router(rams.router, prefix="/api/v1")
