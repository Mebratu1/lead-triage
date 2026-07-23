"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.client import SupabaseClient
from app.routes import health, leads

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle context manager."""
    # Startup
    logger.info(f"Starting LeadTriage API (environment: {settings.environment})")
    logger.info("Supabase configured: %s", bool(settings.supabase_url))
    logger.info("OpenAI model configured: %s", bool(settings.openai_model))
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

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(health.router)
    app.include_router(leads.router)

    logger.info("FastAPI application created successfully")
    return app


app = create_app()
