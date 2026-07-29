"""OpenAI client wrapper for isolated lead classification."""

import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.services.lead_classification import build_classification_messages

logger = logging.getLogger(__name__)


class OpenAIClassificationError(Exception):
    """Raised when OpenAI classification cannot return usable text."""


class OpenAILeadClassificationClient:
    """OpenAI-backed implementation of the lead classification client protocol."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model or settings.openai_model
        self._client = client or AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url or settings.openai_base_url,
            timeout=timeout_seconds or settings.ai_request_timeout_seconds,
        )

    async def classify(self, raw_message: str) -> str:
        """Classify a raw message without logging full customer text."""
        logger.info(
            "Requesting lead classification model=%s message_length=%s",
            self.model,
            len(raw_message),
        )
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=build_classification_messages(raw_message),
            temperature=0,
            response_format={"type": "json_object"},
        )
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError) as exc:
            raise OpenAIClassificationError("invalid classification response") from exc

        if not content:
            raise OpenAIClassificationError("empty classification response")
        return content
