"""Tests for the manual pending-lead classification CLI runner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.jobs.classify_pending_leads import (
    ClassificationJobResult,
    async_main,
    parse_args,
    run_classification_job,
)
from app.models.classification import LeadClassificationBatchResult


class FakeCliClient:
    """Mock classification client used by CLI execution tests."""

    model = "gpt-cli-test"

    async def classify(self, raw_message: str) -> str:
        raise AssertionError("classification should be mocked through process_batch")


class TestClassifyPendingLeadsCliArgs:
    """Argument parser tests."""

    @pytest.mark.unit
    def test_parse_args_uses_default_limit(self):
        """Test default CLI arguments."""
        args = parse_args([])

        assert args.limit == 10
        assert args.dry_run is False

    @pytest.mark.unit
    def test_parse_args_accepts_limit_and_dry_run(self):
        """Test explicit limit and dry-run options."""
        args = parse_args(["--limit", "25", "--dry-run"])

        assert args.limit == 25
        assert args.dry_run is True

    @pytest.mark.unit
    @pytest.mark.parametrize("limit", ["0", "-1", "101", "not-a-number"])
    def test_parse_args_rejects_invalid_limits(self, limit: str):
        """Test invalid limits fail argument parsing."""
        with pytest.raises(SystemExit):
            parse_args(["--limit", limit])


class TestClassifyPendingLeadsCliExecution:
    """Manual CLI execution-flow tests."""

    @pytest.mark.unit
    def test_dry_run_fetches_pending_leads_without_processing(self, caplog):
        """Test dry-run avoids OpenAI and persistence updates."""
        calls: dict[str, Any] = {"closed": False}
        database = object()
        raw_message = "Private customer text 301-555-0144"

        async def db_factory():
            calls["db_factory"] = True
            return database

        async def db_close():
            calls["closed"] = True

        async def fetch_pending(db, limit):
            calls["fetch"] = {"db": db, "limit": limit}
            return [
                {
                    "id": "lead-1",
                    "raw_message": raw_message,
                    "classification_status": "pending",
                }
            ]

        async def process_batch(db, client, limit):
            raise AssertionError("dry-run must not process the batch")

        with caplog.at_level(logging.INFO):
            result = asyncio.run(
                run_classification_job(
                    limit=5,
                    dry_run=True,
                    db_factory=db_factory,
                    db_close=db_close,
                    fetch_pending=fetch_pending,
                    process_batch=process_batch,
                )
            )

        assert result == ClassificationJobResult(dry_run=True, fetched=1)
        assert calls["fetch"] == {"db": database, "limit": 5}
        assert calls["closed"] is True
        assert raw_message not in caplog.text
        assert "301-555-0144" not in caplog.text

    @pytest.mark.unit
    def test_normal_run_processes_batch_with_injected_client(self):
        """Test non-dry-run delegates to the worker orchestration service."""
        calls: dict[str, Any] = {"closed": False}
        database = object()
        client = FakeCliClient()

        async def db_factory():
            return database

        async def db_close():
            calls["closed"] = True

        async def fetch_pending(db, limit):
            raise AssertionError("normal run should use process_batch")

        async def process_batch(db, active_client, limit):
            calls["process"] = {
                "db": db,
                "client": active_client,
                "limit": limit,
            }
            return LeadClassificationBatchResult(
                fetched=3,
                saved=2,
                classified=1,
                failed=1,
                skipped=1,
                errors=0,
                results=[],
            )

        result = asyncio.run(
            run_classification_job(
                limit=3,
                dry_run=False,
                client=client,
                db_factory=db_factory,
                db_close=db_close,
                fetch_pending=fetch_pending,
                process_batch=process_batch,
            )
        )

        assert result == ClassificationJobResult(
            dry_run=False,
            fetched=3,
            saved=2,
            classified=1,
            failed=1,
            skipped=1,
            errors=0,
        )
        assert calls["process"] == {
            "db": database,
            "client": client,
            "limit": 3,
        }
        assert calls["closed"] is True

    @pytest.mark.unit
    def test_provided_database_is_not_closed_by_runner(self):
        """Test caller-owned database objects are not closed by the CLI helper."""
        database = object()
        calls = {"closed": False}

        async def db_close():
            calls["closed"] = True

        async def fetch_pending(db, limit):
            return []

        result = asyncio.run(
            run_classification_job(
                limit=1,
                dry_run=True,
                db=database,
                db_close=db_close,
                fetch_pending=fetch_pending,
            )
        )

        assert result == ClassificationJobResult(dry_run=True, fetched=0)
        assert calls["closed"] is False

    @pytest.mark.unit
    def test_async_main_returns_nonzero_on_batch_errors(self, monkeypatch):
        """Test CLI exits non-zero when the batch reports persistence errors."""
        async def fake_run_classification_job(limit: int, dry_run: bool):
            assert limit == 2
            assert dry_run is False
            return ClassificationJobResult(
                dry_run=False,
                fetched=2,
                saved=1,
                errors=1,
            )

        monkeypatch.setattr(
            "app.jobs.classify_pending_leads.run_classification_job",
            fake_run_classification_job,
        )

        exit_code = asyncio.run(async_main(["--limit", "2"]))

        assert exit_code == 1

    @pytest.mark.unit
    def test_async_main_logs_safe_error_without_raw_message(self, monkeypatch, caplog):
        """Test CLI failure logs only the exception type."""
        raw_message = "Private customer text 301-555-0144"

        async def fake_run_classification_job(limit: int, dry_run: bool):
            raise RuntimeError(raw_message)

        monkeypatch.setattr(
            "app.jobs.classify_pending_leads.run_classification_job",
            fake_run_classification_job,
        )

        with caplog.at_level(logging.ERROR):
            exit_code = asyncio.run(async_main(["--dry-run"]))

        assert exit_code == 1
        assert "RuntimeError" in caplog.text
        assert raw_message not in caplog.text
        assert "301-555-0144" not in caplog.text
