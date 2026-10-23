"""Live Gemini client (google-genai SDK), used only when AI_MODE=live.

Sends one batch of context packs per request and asks for structured JSON output
(response_mime_type "application/json" + a JSON schema). Rate-limit errors (429 /
RESOURCE_EXHAUSTED) are raised as AIRateLimited with the retry delay the API returned;
the AI step decides whether to wait and retry.

Free tier: Google may use prompts to improve its models, so only synthetic data is ever sent.
"""

import json
import re

from google import genai
from google.genai import errors, types

from planner.ai.base import AIClient, AIRateLimited, AIServerBusy
from planner.config import env

SYSTEM_PROMPT = """You assist Raghav, Senior Demand Planner at Freshroots (packaged snacks and staples sold
through distributors in Maharashtra and Goa). A rule engine has flagged each SKU below as an exception that
needs human judgment.

For EACH SKU, using only the data in its context pack:
- likely_cause: the most likely business reason for the exception, in one short phrase.
- suggested_qty_min / suggested_qty_max: a RANGE of units for the next purchase order. Raghav sets the final
  quantity; never give a single number. Use 0 and 0 if the best action is not to order.
- confidence: low, medium or high.
- reasoning: at most 3 sentences, citing the numbers you relied on.
- questions_for_raghav: 0-2 short questions whose answers would change the decision.

Consider: stock already on order, the festival uplift, perishability (perishable stock beyond about 4 weeks
risks write-off), distributor-level changes, how similar SKUs are behaving, and how Raghav handled similar
exceptions before. Return exactly one object per sku_id given, and nothing else."""

RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "sku_id": {"type": "string"},
            "likely_cause": {"type": "string"},
            "suggested_qty_min": {"type": "integer"},
            "suggested_qty_max": {"type": "integer"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasoning": {"type": "string"},
            "questions_for_raghav": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sku_id", "likely_cause", "suggested_qty_min", "suggested_qty_max", "confidence",
                     "reasoning", "questions_for_raghav"],
    },
}


def retry_delay_seconds(error: errors.APIError) -> float | None:
    """Read RetryInfo.retryDelay (e.g. "37s") from a 429 error's details, if present."""
    body = error.details if isinstance(error.details, dict) else {}
    for detail in body.get("error", body).get("details", []) or []:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            match = re.match(r"([\d.]+)s", str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


class GeminiClient(AIClient):
    uses_quota = True

    def __init__(self, model: str, api_key: str | None = None, client=None, timeout_seconds: float = 90):
        self.model = model
        if client is None:
            api_key = api_key or env("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("AI_MODE=live needs GEMINI_API_KEY in .env")
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)))
        self.client = client

    def generate(self, packs: list[dict]) -> str:
        prompt = "Exception SKUs (one context pack each):\n" + json.dumps(packs, separators=(",", ":"))
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_json_schema=RESPONSE_SCHEMA,
                    temperature=0.2,
                ),
            )
        except errors.APIError as e:
            if e.code == 429 or e.status == "RESOURCE_EXHAUSTED":
                raise AIRateLimited(f"{e.code} {e.status}", retry_delay_seconds(e)) from e
            if isinstance(e, errors.ServerError):          # 500 / 503 high demand / 504 deadline
                raise AIServerBusy(f"{e.code} {e.status}") from e
            raise
        return response.text or ""
