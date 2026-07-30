"""Deterministic tests for public lead intake rate limiting."""

import asyncio

import pytest

from app.services.rate_limiter import (
    LeadIntakeRateLimiter,
    TrustedProxyClientIpResolver,
)


@pytest.mark.unit
def test_minute_limit_resets_at_window_boundary():
    """Test minute-window decisions and Retry-After are clock deterministic."""
    now = [0.0]
    limiter = LeadIntakeRateLimiter(
        per_minute=2,
        per_hour=100,
        clock=lambda: now[0],
    )

    assert asyncio.run(limiter.check("client-1")).allowed is True
    assert asyncio.run(limiter.check("client-1")).allowed is True
    limited = asyncio.run(limiter.check("client-1"))
    assert limited.allowed is False
    assert limited.retry_after_seconds == 60

    now[0] = 60.0
    assert asyncio.run(limiter.check("client-1")).allowed is True


@pytest.mark.unit
def test_hour_limit_is_independent_per_client():
    """Test the hourly limit applies per client and releases exactly on expiry."""
    now = [10.0]
    limiter = LeadIntakeRateLimiter(
        per_minute=100,
        per_hour=2,
        clock=lambda: now[0],
    )

    assert asyncio.run(limiter.check("client-1")).allowed is True
    now[0] = 70.0
    assert asyncio.run(limiter.check("client-1")).allowed is True
    assert asyncio.run(limiter.check("client-2")).allowed is True

    limited = asyncio.run(limiter.check("client-1"))
    assert limited.allowed is False
    assert limited.retry_after_seconds == 3540

    now[0] = 3610.0
    assert asyncio.run(limiter.check("client-1")).allowed is True


@pytest.mark.unit
def test_global_cleanup_runs_only_after_configured_interval():
    """Test stale-client scans are not performed on every request."""
    now = [0.0]
    limiter = LeadIntakeRateLimiter(
        per_minute=100,
        per_hour=100,
        clock=lambda: now[0],
        cleanup_interval_seconds=7200,
    )

    async def exercise_limiter() -> None:
        await limiter.check("client-1")
        now[0] = 3601.0
        await limiter.check("client-2")
        assert "client-1" in limiter._requests

        now[0] = 7200.0
        await limiter.check("client-3")
        assert "client-1" not in limiter._requests
        assert "client-2" in limiter._requests

    asyncio.run(exercise_limiter())


class TestTrustedProxyClientIpResolver:
    """Trusted proxy and spoof-resistant forwarded-chain tests."""

    @pytest.mark.unit
    def test_untrusted_peer_cannot_override_client_ip(self):
        """Test public clients cannot supply their own limiter identity."""
        resolver = TrustedProxyClientIpResolver(["10.0.0.0/8"])

        assert (
            resolver.resolve(
                peer_host="198.51.100.20",
                forwarded_for="203.0.113.99",
            )
            == "198.51.100.20"
        )

    @pytest.mark.unit
    def test_trusted_proxy_resolves_nearest_untrusted_hop(self):
        """Test right-to-left resolution rejects a spoofed leftmost address."""
        resolver = TrustedProxyClientIpResolver(
            ["10.0.0.0/8", "192.0.2.0/24"]
        )

        assert (
            resolver.resolve(
                peer_host="10.0.0.5",
                forwarded_for="203.0.113.250, 198.51.100.8, 192.0.2.10",
            )
            == "198.51.100.8"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "forwarded_for",
        [
            "not-an-ip",
            "198.51.100.8,,192.0.2.10",
            ",".join(["192.0.2.10"] * 33),
        ],
    )
    def test_malformed_or_oversized_chain_falls_back_to_peer(
        self,
        forwarded_for: str,
    ):
        """Test invalid forwarded headers cannot choose a client bucket."""
        resolver = TrustedProxyClientIpResolver(["10.0.0.0/8"])

        assert (
            resolver.resolve(
                peer_host="10.0.0.5",
                forwarded_for=forwarded_for,
            )
            == "10.0.0.5"
        )
