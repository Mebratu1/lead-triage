"""Tests for classified lead persistence updates."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.classification import LeadClassified, LeadUrgency
from app.repositories.lead_repository import (
    LeadRepositoryUpdateConflict,
    LeadRepositoryUpdateError,
    release_lead_classification_claim,
    update_lead_classification,
)
from app.services.lead_persistence import (
    LeadClassificationUpdateConflict,
    persist_lead_classification,
    release_lead_classification_for_retry,
)

COMPLETED_AT = datetime(2026, 7, 27, 15, 30, tzinfo=UTC)
COMPLETED_AT_ISO = COMPLETED_AT.isoformat()


class FakeClassificationUpdateQuery:
    """Small Supabase update-query test double."""

    def __init__(self, database: FakeClassificationUpdateDatabase):
        self.database = database
        self.payload: dict[str, Any] | None = None
        self.filters: dict[str, Any] = {}

    def update(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        return self

    def eq(self, field: str, value: Any):
        self.filters[field] = value
        return self

    def select(self, *args):
        return self

    async def execute(self):
        if self.payload is None:
            raise RuntimeError("missing update payload")
        return self.database.execute_update(self.payload, self.filters)


class FakeClassificationUpdateDatabase:
    """In-memory stand-in for classification updates on the leads table."""

    def __init__(
        self,
        row: dict[str, Any] | None = None,
        fail_update: bool = False,
        failure_message: str = "database failure",
    ):
        self.row = dict(row or pending_lead())
        self.fail_update = fail_update
        self.failure_message = failure_message
        self.updated_payloads: list[dict[str, Any]] = []
        self.update_count = 0

    def table(self, name: str):
        assert name == "leads"
        return FakeClassificationUpdateQuery(self)

    def execute_update(self, payload: dict[str, Any], filters: dict[str, Any]):
        self.update_count += 1
        if self.fail_update:
            raise RuntimeError(self.failure_message)

        if not self._matches_filters(filters):
            return SimpleNamespace(data=[])

        self.updated_payloads.append(dict(payload))
        self.row.update(payload)
        return SimpleNamespace(data=[dict(self.row)])

    def _matches_filters(self, filters: dict[str, Any]) -> bool:
        return all(self.row.get(field) == value for field, value in filters.items())


def pending_lead(**overrides) -> dict[str, Any]:
    """Build a lead row that is ready for classification."""
    row = {
        "id": "44444444-4444-4444-8444-444444444444",
        "source": "website",
        "classification_status": "pending",
        "created_at": "2026-07-27T14:00:00+00:00",
        "raw_message": "I need emergency plumbing service today.",
        "customer_name": None,
        "email": None,
        "phone": None,
        "requested_service": None,
        "urgency": None,
        "lead_score": None,
        "ai_summary": None,
        "classification_error": None,
        "classified_at": None,
        "classification_model": None,
        "classification_claimed_at": None,
        "classification_claimed_by": None,
        "classification_attempt_count": 0,
        "last_classification_error": None,
        "next_classification_attempt_at": None,
    }
    row.update(overrides)
    return row


def classified_result(**overrides) -> LeadClassified:
    """Build a classified lead result."""
    payload = {
        "customer_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "301-555-0144",
        "requested_service": "emergency plumbing",
        "urgency": LeadUrgency.HOT,
        "lead_score": 92,
        "ai_summary": "Customer needs emergency plumbing service today.",
    }
    payload.update(overrides)
    return LeadClassified(**payload)


class TestLeadClassificationRepository:
    """Repository-level classification update tests."""

    @pytest.mark.unit
    def test_updates_pending_lead_with_classified_fields(self):
        """Test classified values are persisted onto an existing pending lead."""
        database = FakeClassificationUpdateDatabase()
        classification = classified_result()

        updated = asyncio.run(
            update_lead_classification(
                db=database,
                lead_id=database.row["id"],
                classification=classification,
                classification_model="gpt-test",
                classified_at=COMPLETED_AT,
            )
        )

        assert updated["classification_status"] == "classified"
        assert updated["customer_name"] == "Jane Doe"
        assert updated["email"] == "jane@example.com"
        assert updated["phone"] == "301-555-0144"
        assert updated["requested_service"] == "emergency plumbing"
        assert updated["urgency"] == "hot"
        assert updated["lead_score"] == 92
        assert updated["ai_summary"] == (
            "Customer needs emergency plumbing service today."
        )
        assert updated["classification_error"] is None
        assert updated["classified_at"] == COMPLETED_AT_ISO
        assert updated["classification_model"] == "gpt-test"
        assert database.updated_payloads == [
            {
                "customer_name": "Jane Doe",
                "email": "jane@example.com",
                "phone": "301-555-0144",
                "requested_service": "emergency plumbing",
                "urgency": "hot",
                "lead_score": 92,
                "ai_summary": "Customer needs emergency plumbing service today.",
                "classification_error": None,
                "classified_at": COMPLETED_AT_ISO,
                "classification_model": "gpt-test",
                "classification_claimed_at": None,
                "classification_claimed_by": None,
                "last_classification_error": None,
                "next_classification_attempt_at": None,
                "classification_status": "classified",
            }
        ]

    @pytest.mark.unit
    def test_failed_classification_clears_extracted_fields_and_sets_failed_status(self):
        """Test failed classification persists a safe status and note."""
        database = FakeClassificationUpdateDatabase()
        classification = LeadClassified.failed("invalid_json")

        updated = asyncio.run(
            update_lead_classification(
                db=database,
                lead_id=database.row["id"],
                classification=classification,
                classification_model="gpt-test",
                classified_at=COMPLETED_AT,
            )
        )

        assert updated["classification_status"] == "failed"
        assert updated["customer_name"] is None
        assert updated["email"] is None
        assert updated["phone"] is None
        assert updated["requested_service"] is None
        assert updated["urgency"] is None
        assert updated["lead_score"] is None
        assert updated["ai_summary"] is None
        assert updated["classification_error"] == "invalid_json"
        assert updated["classified_at"] == COMPLETED_AT_ISO
        assert updated["classification_model"] == "gpt-test"
        assert updated["classification_claimed_at"] is None
        assert updated["classification_claimed_by"] is None
        assert updated["last_classification_error"] == "invalid_json"
        assert updated["next_classification_attempt_at"] is None

    @pytest.mark.unit
    def test_failed_classification_does_not_persist_untrusted_error_text(self):
        """Test arbitrary error reasons are not saved verbatim."""
        database = FakeClassificationUpdateDatabase()
        classification = LeadClassified.failed(
            "raw customer text with test-service-role-key"
        )

        updated = asyncio.run(
            update_lead_classification(
                db=database,
                lead_id=database.row["id"],
                classification=classification,
                classified_at=COMPLETED_AT,
            )
        )

        assert updated["classification_status"] == "failed"
        assert updated["classification_error"] == "classification_failed"
        assert "test-service-role-key" not in repr(updated)
        assert "raw customer text" not in repr(updated)

    @pytest.mark.unit
    def test_atomic_update_requires_pending_status(self):
        """Test already-processed rows are not overwritten."""
        database = FakeClassificationUpdateDatabase(
            row=pending_lead(classification_status="classified")
        )
        classification = classified_result(lead_score=100)

        with pytest.raises(LeadRepositoryUpdateConflict):
            asyncio.run(
                update_lead_classification(
                    db=database,
                    lead_id=database.row["id"],
                    classification=classification,
                    classified_at=COMPLETED_AT,
                    claim_owner_id="worker-1",
                )
            )

        assert database.row["classification_status"] == "classified"
        assert database.row["lead_score"] is None
        assert database.updated_payloads == []

    @pytest.mark.unit
    def test_atomic_update_requires_claim_owner_when_supplied(self):
        """Test claimed rows can only be completed by the owner."""
        database = FakeClassificationUpdateDatabase(
            row=pending_lead(classification_claimed_by="worker-2")
        )

        with pytest.raises(LeadRepositoryUpdateConflict):
            asyncio.run(
                update_lead_classification(
                    db=database,
                    lead_id=database.row["id"],
                    classification=classified_result(),
                    classified_at=COMPLETED_AT,
                    claim_owner_id="worker-1",
                )
            )

        assert database.row["classification_status"] == "pending"
        assert database.updated_payloads == []

    @pytest.mark.unit
    def test_release_claim_for_retry_clears_claim_and_sets_backoff(self):
        """Test retryable failures release ownership with safe retry metadata."""
        database = FakeClassificationUpdateDatabase(
            row=pending_lead(classification_claimed_by="worker-1")
        )

        updated = asyncio.run(
            release_lead_classification_claim(
                db=database,
                lead_id=database.row["id"],
                worker_id="worker-1",
                error_reason="classification_client_error",
                retry_after_seconds=60,
                now=COMPLETED_AT,
            )
        )

        assert updated["classification_status"] == "pending"
        assert updated["classification_claimed_at"] is None
        assert updated["classification_claimed_by"] is None
        assert updated["last_classification_error"] == "classification_client_error"
        assert updated["next_classification_attempt_at"] == (
            "2026-07-27T15:31:00+00:00"
        )

    @pytest.mark.unit
    def test_release_claim_for_retry_requires_claim_owner(self):
        """Test a different worker cannot release another worker's claim."""
        database = FakeClassificationUpdateDatabase(
            row=pending_lead(classification_claimed_by="worker-2")
        )

        with pytest.raises(LeadRepositoryUpdateConflict):
            asyncio.run(
                release_lead_classification_claim(
                    db=database,
                    lead_id=database.row["id"],
                    worker_id="worker-1",
                    error_reason="classification_client_error",
                    retry_after_seconds=60,
                    now=COMPLETED_AT,
                )
            )

    @pytest.mark.unit
    def test_database_update_failure_raises_controlled_error(self):
        """Test low-level update failures are mapped to repository errors."""
        database = FakeClassificationUpdateDatabase(
            fail_update=True,
            failure_message="update failed with test-service-role-key and SQL details",
        )

        with pytest.raises(LeadRepositoryUpdateError):
            asyncio.run(
                update_lead_classification(
                    db=database,
                    lead_id=database.row["id"],
                    classification=classified_result(),
                    classified_at=COMPLETED_AT,
                )
            )


class TestLeadClassificationPersistence:
    """Persistence-service classification update tests."""

    @pytest.mark.integration
    def test_persistence_wrapper_updates_pending_lead(self):
        """Test service wrapper saves repository classification results."""
        database = FakeClassificationUpdateDatabase()

        updated = asyncio.run(
            persist_lead_classification(
                db=database,
                lead_id=database.row["id"],
                classification=classified_result(lead_score=75),
                classification_model="gpt-test",
                classified_at=COMPLETED_AT,
                claim_owner_id=None,
            )
        )

        assert updated["classification_status"] == "classified"
        assert updated["lead_score"] == 75
        assert updated["classification_model"] == "gpt-test"
        assert database.update_count == 1

    @pytest.mark.integration
    def test_persistence_wrapper_maps_atomic_update_conflict(self):
        """Test service wrapper exposes a persistence-layer conflict."""
        database = FakeClassificationUpdateDatabase(
            row=pending_lead(classification_status="failed")
        )

        with pytest.raises(LeadClassificationUpdateConflict):
            asyncio.run(
                persist_lead_classification(
                    db=database,
                    lead_id=database.row["id"],
                    classification=classified_result(),
                    classified_at=COMPLETED_AT,
                )
            )

    @pytest.mark.integration
    def test_persistence_wrapper_releases_claim_for_retry(self):
        """Test service wrapper maps retry release results."""
        database = FakeClassificationUpdateDatabase(
            row=pending_lead(classification_claimed_by="worker-1")
        )

        updated = asyncio.run(
            release_lead_classification_for_retry(
                db=database,
                lead_id=database.row["id"],
                worker_id="worker-1",
                error_reason="untrusted test-service-role-key details",
                retry_after_seconds=30,
                now=COMPLETED_AT,
            )
        )

        assert updated["last_classification_error"] == "classification_retry_failed"
        assert "test-service-role-key" not in repr(updated)
