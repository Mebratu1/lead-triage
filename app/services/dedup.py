"""Deduplication service for detecting duplicate leads."""

import inspect
import logging
from datetime import datetime, timedelta, timezone

from supabase import AsyncClient

from app.models.lead import Lead

logger = logging.getLogger(__name__)


async def _resolve(value):
    """Await AsyncMock-style builder calls while keeping real Supabase calls unchanged."""
    if inspect.isawaitable(value):
        return await value
    return value


class DeduplicationService:
    """Handles lead deduplication logic."""

    def __init__(self, db: AsyncClient, dedup_window_days: int = 7):
        """Initialize deduplication service."""
        self.db = db
        self.dedup_window_days = dedup_window_days
        logger.info(f"Initialized DeduplicationService with window: {dedup_window_days} days")

    async def check_duplicate_email(self, email: str) -> Lead | None:
        """Check if lead with email exists within dedup window."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.dedup_window_days)

        try:
            logger.debug(f"Checking for duplicate email: {email} (since {cutoff_date.date()})")
            query = await _resolve(self.db.table("leads"))
            query = await _resolve(query.select("*"))
            query = await _resolve(query.eq("email", email.lower()))
            query = await _resolve(query.gte("created_at", cutoff_date.isoformat()))
            response = await _resolve(query.execute())

            if response.data and len(response.data) > 0:
                lead_data = response.data[0]
                lead = Lead.from_dict(lead_data)
                logger.info(f"Duplicate email found: {email} -> lead_id: {lead.id}")
                return lead

            logger.debug(f"No duplicate email found: {email}")
            return None
        except Exception as e:
            logger.error(f"Deduplication email check failed: {str(e)}")
            raise

    async def check_duplicate_phone(self, phone: str) -> Lead | None:
        """Check if lead with phone exists within dedup window."""
        if not phone:
            logger.debug("Phone is empty, skipping phone deduplication check")
            return None

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.dedup_window_days)

        try:
            logger.debug(f"Checking for duplicate phone: {phone} (since {cutoff_date.date()})")
            query = await _resolve(self.db.table("leads"))
            query = await _resolve(query.select("*"))
            query = await _resolve(query.eq("phone", phone))
            query = await _resolve(query.gte("created_at", cutoff_date.isoformat()))
            response = await _resolve(query.execute())

            if response.data and len(response.data) > 0:
                lead_data = response.data[0]
                lead = Lead.from_dict(lead_data)
                logger.info(f"Duplicate phone found: {phone} -> lead_id: {lead.id}")
                return lead

            logger.debug(f"No duplicate phone found: {phone}")
            return None
        except Exception as e:
            logger.error(f"Deduplication phone check failed: {str(e)}")
            raise

    async def log_duplicate(
        self,
        original_lead_id: str,
        duplicate_lead_id: str,
        match_type: str,
        similarity_score: float = 1.0,
    ) -> None:
        """Log duplicate detection in audit table."""
        try:
            logger.debug(
                f"Logging duplicate: original={original_lead_id}, duplicate={duplicate_lead_id}, type={match_type}"
            )
            table = await _resolve(self.db.table("duplicate_log"))
            query = await _resolve(table.insert({
                "original_lead_id": original_lead_id,
                "duplicate_lead_id": duplicate_lead_id,
                "match_type": match_type,
                "similarity_score": similarity_score,
            }))
            await _resolve(query.execute())
            logger.info(f"Duplicate logged: {original_lead_id} -> {duplicate_lead_id}")
        except Exception as e:
            logger.error(f"Failed to log duplicate: {str(e)}")
            raise

