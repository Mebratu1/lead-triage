"""Prompt and parser layer for lead classification."""

import json
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import ValidationError

from app.models.classification import LeadClassificationPayload, LeadClassified

CLASSIFICATION_SYSTEM_PROMPT = """
You classify unstructured sales lead inquiries.
Return only a JSON object with these keys:
customer_name, email, phone, requested_service, urgency, lead_score, ai_summary.
Use null when a field is unknown.
urgency must be one of hot, warm, cold, or null.
lead_score must be an integer from 0 to 100, or null.
Do not invent customer details that are not present in the message.
""".strip()


class LeadClassificationClient(Protocol):
    """Interface implemented by real or mocked lead classification clients."""

    async def classify(self, raw_message: str) -> str:
        """Return raw model output for a lead message."""


def build_classification_messages(raw_message: str) -> list[dict[str, str]]:
    """Build chat messages without logging or rewriting customer content."""
    return [
        {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": raw_message},
    ]


def _strip_markdown_json_fence(model_output: str) -> str:
    """Remove common markdown JSON fences around model output."""
    stripped = model_output.strip()
    fenced = re.fullmatch(
        r"```(?:json|JSON)?\s*(?P<body>.*?)\s*```",
        stripped,
        flags=re.DOTALL,
    )
    if fenced:
        return fenced.group("body").strip()
    return stripped


def _extract_json_object(model_output: str) -> str:
    """Return the JSON object text from a model response."""
    cleaned = _strip_markdown_json_fence(model_output)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return cleaned
    return cleaned[start : end + 1]


def parse_classification_response(model_output: str) -> LeadClassified:
    """Parse and validate model output into a safe classification contract."""
    try:
        parsed = json.loads(_extract_json_object(model_output))
    except json.JSONDecodeError:
        return LeadClassified.failed("invalid_json")

    if not isinstance(parsed, dict):
        return LeadClassified.failed("invalid_json_shape")

    try:
        payload = LeadClassificationPayload.model_validate(parsed)
        return LeadClassified.from_payload(payload)
    except ValidationError:
        return LeadClassified.failed("invalid_classification_payload")


async def classify_raw_message(
    raw_message: str,
    client: LeadClassificationClient,
) -> LeadClassified:
    """Classify a raw message through a mocked or real classification client."""
    model_output = await client.classify(raw_message)
    return parse_classification_response(model_output)


def validate_prompt_messages(messages: Sequence[dict[str, str]]) -> bool:
    """Validate prompt message shape for tests and future callers."""
    return all(
        message.get("role") in {"system", "user", "assistant"}
        and isinstance(message.get("content"), str)
        for message in messages
    )
