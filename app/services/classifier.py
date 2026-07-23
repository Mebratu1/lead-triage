"""Lead classification service using LLM."""

import json
import logging

from openai import AsyncOpenAI

from app.models.lead import Lead

logger = logging.getLogger(__name__)


class ClassificationService:
    """Handles lead classification using LLM."""

    def __init__(self, api_key: str, model: str = "gpt-4-turbo-preview"):
        """Initialize classification service."""
        self.api_key = api_key
        self.client: AsyncOpenAI | None = None
        self.model = model
        logger.info(f"Initialized ClassificationService with model: {model}")

    def _get_client(self) -> AsyncOpenAI:
        """Create the OpenAI client only when an API call is needed."""
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.api_key)
        return self.client

    async def classify_lead(self, lead: Lead) -> dict:
        """
        Classify a lead using LLM.

        Returns:
            dict with keys: lead_score, status, tags, rationale
        """
        prompt = self._build_prompt(lead)

        try:
            logger.debug(f"Calling OpenAI API for lead {lead.id}: {lead.email}")
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt(),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                timeout=30.0,
            )

            result_text = response.choices[0].message.content
            logger.debug(f"OpenAI response for {lead.email}: {result_text[:200]}...")
            
            result = self._parse_classification_response(result_text)
            logger.info(f"Classification successful for {lead.email}: score={result['lead_score']}")
            return result

        except Exception as e:
            logger.error(f"Classification failed for lead {lead.id} ({lead.email}): {str(e)}")
            raise

    def _get_system_prompt(self) -> str:
        """Get the system prompt for LLM."""
        return """You are an expert lead qualification specialist. Your job is to evaluate sales leads and determine their quality and readiness for sales outreach.

Analyze leads based on:
1. Company fit - Does the company align with typical customer profiles?
2. Decision-making authority - Does the job title suggest decision-making power?
3. Engagement quality - Is this a real, engaged prospect?
4. Budget signals - Does company size/type suggest budget availability?
5. Timeline urgency - Any signals about buying urgency?

Provide accurate, actionable classifications. Respond ONLY with valid JSON."""

    def _build_prompt(self, lead: Lead) -> str:
        """Build classification prompt for LLM."""
        return f"""Classify this sales lead on the following criteria:

LEAD DATA:
- Name: {lead.first_name} {lead.last_name}
- Email: {lead.email}
- Phone: {lead.phone or 'Not provided'}
- Company: {lead.company or 'Not provided'}
- Job Title: {lead.job_title or 'Not provided'}
- Source: {lead.source or 'Unknown'}

CLASSIFICATION CRITERIA:
1. LEAD SCORE (0-100): Overall sales-readiness score
   - 80-100: Highly qualified, ready for sales outreach
   - 60-79: Good fit, needs nurturing
   - 40-59: Potential lead, low priority
   - 0-39: Not qualified or likely spam

2. STATUS: One of [qualified, needs_nurture, low_value]
   - qualified: Score 60+, ready for immediate action
   - needs_nurture: Score 40-59, needs engagement/education
   - low_value: Score 0-39, not worth effort

3. TAGS: Multiple tags from [sales_ready, needs_nurture, spam, low_value, high_priority]
   - sales_ready: Immediate outreach recommended
   - needs_nurture: Add to nurture campaign
   - high_priority: VIP account or large opportunity
   - spam: Invalid/suspicious
   - low_value: Not worth pursuing

4. RATIONALE: Brief explanation (1-2 sentences) of the score and key decision factors

RESPOND IN THIS EXACT JSON FORMAT (ONLY JSON, NO OTHER TEXT):
{{
    "lead_score": <0-100>,
    "status": "<qualified|needs_nurture|low_value>",
    "tags": ["<tag1>", "<tag2>"],
    "rationale": "<brief explanation of score and key factors>"
}}

IMPORTANT: Respond ONLY with valid JSON, no additional text."""

    def _parse_classification_response(self, response_text: str) -> dict:
        """Parse LLM response and extract classification."""
        try:
            # Extract JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1

            if json_start == -1 or json_end == 0:
                logger.error(f"No JSON found in response: {response_text[:200]}")
                raise ValueError("No JSON found in response")

            json_str = response_text[json_start:json_end]
            logger.debug(f"Extracted JSON: {json_str[:200]}")
            result = json.loads(json_str)

            # Validate required fields
            required_fields = ["lead_score", "status", "tags", "rationale"]
            missing_fields = [f for f in required_fields if f not in result]
            if missing_fields:
                raise ValueError(f"Missing required fields: {missing_fields}")

            # Validate lead_score
            if not isinstance(result["lead_score"], int) or not (0 <= result["lead_score"] <= 100):
                raise ValueError(f"Invalid lead_score: {result['lead_score']} (must be 0-100)")

            # Validate status
            valid_statuses = ["qualified", "needs_nurture", "low_value"]
            if result["status"] not in valid_statuses:
                raise ValueError(f"Invalid status: {result['status']} (must be one of {valid_statuses})")

            # Validate tags
            valid_tags = {"sales_ready", "needs_nurture", "spam", "low_value", "high_priority"}
            invalid_tags = set(result.get("tags", [])) - valid_tags
            if invalid_tags:
                logger.warning(f"Invalid tags removed: {invalid_tags}")
                result["tags"] = [t for t in result.get("tags", []) if t in valid_tags]

            # Validate rationale
            if not isinstance(result.get("rationale"), str) or len(result["rationale"].strip()) == 0:
                raise ValueError("Rationale must be a non-empty string")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}\nResponse: {response_text[:300]}")
            raise ValueError(f"Invalid JSON in LLM response: {str(e)}")
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            raise

