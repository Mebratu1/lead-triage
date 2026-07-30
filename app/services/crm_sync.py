"""Outbound CRM synchronization boundary."""

from typing import Protocol

from app.models.schemas import LeadPublicResponse


class LeadCrmDispatcher(Protocol):
    """Interface for CRM/webhook delivery implementations."""

    async def sync_lead(self, lead: LeadPublicResponse) -> None:
        """Send one admin-safe lead payload to an external CRM."""


class NoopLeadCrmDispatcher:
    """Default dispatcher used until a real CRM adapter is configured."""

    async def sync_lead(self, lead: LeadPublicResponse) -> None:
        """Accept the lead without making an external network call."""
        return None


async def dispatch_lead_to_crm(
    lead: LeadPublicResponse,
    dispatcher: LeadCrmDispatcher | None = None,
) -> None:
    """Dispatch one lead through the configured CRM delivery boundary."""
    active_dispatcher = dispatcher or NoopLeadCrmDispatcher()
    await active_dispatcher.sync_lead(lead)
