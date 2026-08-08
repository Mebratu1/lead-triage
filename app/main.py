"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.client import SupabaseClient
from app.middleware import RequestBodySizeLimitMiddleware
from app.api.routes import leads
from app.routes import admin, health
from app.services.rate_limiter import (
    LeadIntakeRateLimiter,
    TrustedProxyClientIpResolver,
)

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle context manager."""
    # Startup
    logger.info(f"Starting LeadTriage API (environment: {settings.environment})")
    logger.info("Supabase configured: %s", bool(settings.supabase_url))
    logger.info("Lead classification connected: False")
    yield
    # Shutdown
    logger.info("Shutting down LeadTriage API")
    await SupabaseClient.close()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.lead_intake_rate_limiter = LeadIntakeRateLimiter(
        per_minute=settings.rate_limit_per_minute,
        per_hour=settings.rate_limit_per_hour,
    )
    app.state.trusted_proxy_client_ip_resolver = TrustedProxyClientIpResolver(
        settings.trusted_proxy_cidrs
    )

    app.add_middleware(
        RequestBodySizeLimitMiddleware,
        max_bytes=settings.request_max_bytes,
    )
    # CORS stays outermost so browser clients receive CORS headers on a 413.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(leads.router, prefix="/api")

    logger.info("FastAPI application created successfully")
    return app


app = create_app()
