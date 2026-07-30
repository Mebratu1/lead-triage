"""Supabase database client initialization."""

import logging
from typing import AsyncGenerator

from supabase import AsyncClient, acreate_client

from app.config import settings

logger = logging.getLogger(__name__)


class SupabaseClientInitializationError(RuntimeError):
    """Raised without retaining provider exception text in server output."""


class SupabaseClient:
    """Manages Supabase connection lifecycle."""

    _instance: AsyncClient | None = None

    @classmethod
    async def get_client(cls) -> AsyncClient:
        """Get or create Supabase async client."""
        if cls._instance is None:
            logger.info("Initializing Supabase client")
            try:
                cls._instance = await acreate_client(
                    supabase_url=settings.supabase_url,
                    supabase_key=settings.supabase_service_role_key,
                )
                logger.info("Supabase client initialized successfully")
            except Exception as exc:
                logger.error(
                    "Failed to initialize Supabase client error_type=%s",
                    exc.__class__.__name__,
                )
                raise SupabaseClientInitializationError(
                    "Supabase client initialization failed"
                ) from None
        return cls._instance

    @classmethod
    async def close(cls) -> None:
        """Close the client connection."""
        if cls._instance is not None:
            logger.info("Closing Supabase connection")
            cls._instance = None


async def get_db() -> AsyncGenerator[AsyncClient, None]:
    """Dependency injection for database client."""
    client = await SupabaseClient.get_client()
    yield client

