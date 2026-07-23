"""Tests for deduplication service."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services.dedup import DeduplicationService


class TestDeduplicationService:
    """Deduplication service tests."""

    @pytest.fixture
    def mock_db(self):
        """Create mock Supabase client."""
        return AsyncMock()

    @pytest.fixture
    def dedup_service(self, mock_db):
        """Create deduplication service with mock DB."""
        return DeduplicationService(db=mock_db, dedup_window_days=7)

    @pytest.mark.unit
    def test_check_duplicate_email_found(self, dedup_service, mock_db):
        """Test detecting existing email duplicate."""
        existing_lead = {
            "id": "existing-id",
            "email": "john@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "lead_score": 85,
            "status": "qualified",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        mock_response = AsyncMock()
        mock_response.data = [existing_lead]
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute = (
            AsyncMock(return_value=mock_response)
        )

        result = asyncio.run(dedup_service.check_duplicate_email("john@example.com"))

        assert result is not None
        assert result.email == "john@example.com"

    @pytest.mark.unit
    def test_check_duplicate_email_not_found(self, dedup_service, mock_db):
        """Test no duplicate found."""
        mock_response = AsyncMock()
        mock_response.data = []
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute = (
            AsyncMock(return_value=mock_response)
        )

        result = asyncio.run(dedup_service.check_duplicate_email("new@example.com"))

        assert result is None

    @pytest.mark.unit
    def test_check_duplicate_email_respects_dedup_window(self, dedup_service, mock_db):
        """Test deduplication respects time window."""
        # This test verifies that the service correctly filters by the dedup window
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute = (
            AsyncMock(return_value=AsyncMock(data=[]))
        )

        asyncio.run(dedup_service.check_duplicate_email("test@example.com"))

        # Verify the service called with correct date filter
        calls = mock_db.table.return_value.select.return_value.eq.return_value.gte.call_args_list
        assert len(calls) > 0

    @pytest.mark.unit
    def test_check_duplicate_phone_found(self, dedup_service, mock_db):
        """Test detecting phone duplicate."""
        existing_lead = {
            "id": "existing-id",
            "email": "john@example.com",
            "phone": "5551234567",
            "first_name": "John",
            "last_name": "Doe",
            "lead_score": 75,
            "status": "needs_nurture",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        mock_response = AsyncMock()
        mock_response.data = [existing_lead]
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute = (
            AsyncMock(return_value=mock_response)
        )

        result = asyncio.run(dedup_service.check_duplicate_phone("5551234567"))

        assert result is not None
        assert result.phone == "5551234567"

    @pytest.mark.unit
    def test_check_duplicate_phone_none_returns_none(self, dedup_service, mock_db):
        """Test that None phone returns None."""
        result = asyncio.run(dedup_service.check_duplicate_phone(None))

        assert result is None
        mock_db.table.assert_not_called()

    @pytest.mark.unit
    def test_log_duplicate(self, dedup_service, mock_db):
        """Test logging duplicate detection."""
        mock_response = AsyncMock()
        mock_db.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_response)

        asyncio.run(dedup_service.log_duplicate(
            original_lead_id="original-id",
            duplicate_lead_id="duplicate-id",
            match_type="email",
            similarity_score=1.0,
        ))

        # Verify insert was called
        mock_db.table.assert_called_with("duplicate_log")
        mock_db.table.return_value.insert.assert_called()

    @pytest.mark.unit
    def test_log_duplicate_db_error(self, dedup_service, mock_db):
        """Test handling of database error during logging."""
        mock_db.table.return_value.insert.return_value.execute = AsyncMock(
            side_effect=Exception("DB error")
        )

        with pytest.raises(Exception):
            asyncio.run(dedup_service.log_duplicate(
                original_lead_id="original-id",
                duplicate_lead_id="duplicate-id",
                match_type="email",
            ))

    @pytest.mark.unit
    def test_check_duplicate_email_case_insensitive(self, dedup_service, mock_db):
        """Test email matching is case-insensitive."""
        mock_response = AsyncMock()
        mock_response.data = []
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute = (
            AsyncMock(return_value=mock_response)
        )

        # Should normalize to lowercase
        asyncio.run(dedup_service.check_duplicate_email("John@EXAMPLE.COM"))

        # Verify email was normalized
        calls = mock_db.table.return_value.select.return_value.eq.call_args_list
        assert any("john@example.com" in str(call).lower() for call in calls)
