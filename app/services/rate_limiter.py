"""Process-local rate limiting for the public lead intake endpoint."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from ipaddress import ip_address, ip_network
import math
import time

MINUTE_SECONDS = 60.0
HOUR_SECONDS = 3600.0
MAX_TRACKED_CLIENTS = 10_000
MAX_FORWARDED_HOPS = 32
OVERFLOW_CLIENT_KEY = "__overflow__"
DEFAULT_CLEANUP_INTERVAL_SECONDS = 60.0

IpAddress = IPv4Address | IPv6Address
IpNetwork = IPv4Network | IPv6Network


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of checking one client against both intake windows."""

    allowed: bool
    retry_after_seconds: int | None = None


class TrustedProxyClientIpResolver:
    """Resolve a client IP only through explicitly trusted reverse proxies."""

    def __init__(self, trusted_proxy_cidrs: list[str]) -> None:
        self._trusted_networks: tuple[IpNetwork, ...] = tuple(
            ip_network(cidr, strict=False)
            for cidr in trusted_proxy_cidrs
        )

    def resolve(
        self,
        *,
        peer_host: str | None,
        forwarded_for: str | None,
    ) -> str:
        """Return the nearest untrusted IP or the direct socket peer."""
        fallback = (peer_host or "unknown").strip() or "unknown"
        try:
            peer_address = ip_address(fallback)
        except ValueError:
            return fallback

        if not self._is_trusted(peer_address) or not forwarded_for:
            return peer_address.compressed

        forwarded_values = [
            value.strip()
            for value in forwarded_for.split(",")
        ]
        if (
            not forwarded_values
            or len(forwarded_values) > MAX_FORWARDED_HOPS
            or any(not value for value in forwarded_values)
        ):
            return peer_address.compressed

        try:
            forwarded_addresses = [
                ip_address(value)
                for value in forwarded_values
            ]
        except ValueError:
            return peer_address.compressed

        for address in reversed(forwarded_addresses):
            if not self._is_trusted(address):
                return address.compressed
        return peer_address.compressed

    def _is_trusted(self, address: IpAddress) -> bool:
        return any(
            address.version == network.version and address in network
            for network in self._trusted_networks
        )


class LeadIntakeRateLimiter:
    """Enforce fixed-cap sliding windows without external infrastructure."""

    def __init__(
        self,
        *,
        per_minute: int,
        per_hour: int,
        clock: Callable[[], float] = time.monotonic,
        cleanup_interval_seconds: float = DEFAULT_CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        if per_minute < 1 or per_hour < 1:
            raise ValueError("rate limits must be positive")
        if cleanup_interval_seconds <= 0:
            raise ValueError("cleanup_interval_seconds must be positive")
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._clock = clock
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._next_cleanup_at = self._clock() + cleanup_interval_seconds
        self._requests: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, client_key: str) -> RateLimitDecision:
        """Check and record one request atomically."""
        now = self._clock()
        async with self._lock:
            if now >= self._next_cleanup_at:
                self._prune_inactive_clients(now)
                self._next_cleanup_at = now + self._cleanup_interval_seconds
            effective_key = self._effective_client_key(client_key)
            timestamps = self._requests.setdefault(effective_key, deque())
            self._prune_timestamps(timestamps, now)

            hour_retry_at = (
                timestamps[0] + HOUR_SECONDS
                if len(timestamps) >= self._per_hour
                else None
            )
            minute_timestamps = [
                timestamp
                for timestamp in timestamps
                if timestamp > now - MINUTE_SECONDS
            ]
            minute_retry_at = (
                minute_timestamps[0] + MINUTE_SECONDS
                if len(minute_timestamps) >= self._per_minute
                else None
            )

            retry_at_candidates = [
                retry_at
                for retry_at in (hour_retry_at, minute_retry_at)
                if retry_at is not None
            ]
            if retry_at_candidates:
                retry_after = max(
                    1,
                    math.ceil(max(retry_at_candidates) - now),
                )
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=retry_after,
                )

            timestamps.append(now)
            return RateLimitDecision(allowed=True)

    def _effective_client_key(self, client_key: str) -> str:
        normalized_key = client_key.strip() or "unknown"
        if (
            normalized_key not in self._requests
            and len(self._requests) >= MAX_TRACKED_CLIENTS
        ):
            return OVERFLOW_CLIENT_KEY
        return normalized_key

    def _prune_inactive_clients(self, now: float) -> None:
        stale_keys: list[str] = []
        for key, timestamps in self._requests.items():
            self._prune_timestamps(timestamps, now)
            if not timestamps:
                stale_keys.append(key)
        for key in stale_keys:
            del self._requests[key]

    @staticmethod
    def _prune_timestamps(timestamps: deque[float], now: float) -> None:
        cutoff = now - HOUR_SECONDS
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
