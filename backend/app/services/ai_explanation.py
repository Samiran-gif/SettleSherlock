"""AI explanation layer.

Turns a finished ``InvestigationResponse`` into a short plain-English summary
for a support agent. The deterministic engine stays the source of truth: this
service never re-decides the status, root cause or confidence, and the model is
given nothing but the facts the engine already produced.

Three safeguards keep it honest:
  * The prompt carries only the investigation result - no CSV rows, no records
    the engine did not use.
  * Any model output containing a number that does not appear in those facts is
    rejected, so invented amounts, timestamps and references cannot reach the
    caller.
  * Every failure path (no key, timeout, HTTP error, unparseable or rejected
    output) degrades to a deterministic summary that states the AI was not used.

Any OpenAI-compatible chat-completions endpoint works. Defaults point at Groq;
switching to Gemini is an ``AI_BASE_URL`` / ``AI_MODEL`` change, not a code
change.
"""

import logging
import re
from typing import List, Optional

import httpx

from app.core.config import Settings, settings as default_settings
from app.schemas.explanation import ExplanationResponse, ExplanationSource
from app.schemas.investigation import InvestigationResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a settlement support assistant for a payments operations team. "
    "You are given the result of a deterministic settlement investigation. "
    "Your only job is to restate those findings in plain English for a "
    "support agent.\n\n"
    "Rules you must follow:\n"
    "- Use ONLY the facts provided. Never add transaction details, amounts, "
    "timestamps, references, causes or conclusions that are not in them.\n"
    "- Do not re-judge the outcome. The status, root cause and recommended "
    "action are already decided; explain them, do not change them.\n"
    "- If the facts say the root cause cannot be confirmed, say exactly that.\n"
    "- Do not speculate about fraud, blame, liability or customer impact.\n"
    "- Write 2 to 4 short sentences of prose. No bullet points, no headings, "
    "no markdown.\n"
    "- Mention any exceptions that need follow-up, and close with the "
    "recommended action."
)

USER_PROMPT_TEMPLATE = (
    "Explain this settlement investigation result to a support agent.\n\n"
    "{facts}"
)

# Matches "5000.00", "94", "10001" - anything the model could have invented.
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


class AIExplanationError(Exception):
    """Raised internally when the provider cannot give us usable text."""


class AIExplanationService:
    """Requests plain-English explanations from an OpenAI-compatible provider.

    ``transport`` is injectable so tests can exercise the real request-building
    and response-parsing code against a stubbed provider.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.settings = settings or default_settings
        self._transport = transport

    # --- Prompt -------------------------------------------------------------

    @staticmethod
    def build_facts(investigation: InvestigationResponse) -> str:
        """Render the investigation result as the only context the model gets."""
        lines = [
            f"Transaction ID: {investigation.transaction_id}",
            f"Settlement status: {investigation.status.value}",
            f"Root cause: {investigation.root_cause}",
            (
                "Investigation confidence: "
                f"{investigation.investigation_confidence} out of 100 "
                "(evidence completeness only, not financial risk)"
            ),
            f"Recommended action: {investigation.recommended_action}",
            "",
            "Evidence:",
        ]
        lines.extend(f"- {item}" for item in investigation.evidence)

        lines.append("")
        if investigation.exceptions:
            lines.append("Exceptions:")
            lines.extend(f"- {item}" for item in investigation.exceptions)
        else:
            lines.append("Exceptions: none")

        return "\n".join(lines)

    # --- Hallucination guard ------------------------------------------------

    @staticmethod
    def unsupported_numbers(text: str, facts: str) -> List[str]:
        """Numbers in the model's text that do not appear in the given facts.

        Catches invented amounts, timestamps and references. Currency symbols
        and thousands separators are stripped first so "$5,000" is checked as
        "5000" rather than being flagged for its formatting.
        """
        normalised = text.replace(",", "").replace("$", "")
        return [
            number
            for number in NUMBER_PATTERN.findall(normalised)
            if number not in facts
        ]

    # --- Provider call ------------------------------------------------------

    def _request_explanation(self, facts: str) -> str:
        """Call the provider and return its text, or raise AIExplanationError."""
        url = f"{self.settings.AI_BASE_URL.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.AI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT_TEMPLATE.format(facts=facts),
                },
            ],
            # Lowest available randomness: we want the same facts to read the
            # same way each time.
            "temperature": 0,
            "max_tokens": self.settings.AI_MAX_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.AI_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(
                timeout=self.settings.AI_TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as error:
            raise AIExplanationError(f"provider request failed ({error})") from error

        if response.status_code != 200:
            raise AIExplanationError(
                f"provider returned HTTP {response.status_code}"
            )

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise AIExplanationError("provider response was not understood") from error

        if not isinstance(content, str) or not content.strip():
            raise AIExplanationError("provider returned an empty explanation")

        return content.strip()

    # --- Fallback -----------------------------------------------------------

    @staticmethod
    def build_fallback_text(investigation: InvestigationResponse) -> str:
        """Summarise the investigation deterministically, with no model.

        Says up front that the AI explanation is unavailable, so a client that
        only renders ``explanation`` still tells the agent the truth.
        """
        parts = [
            "AI explanation is unavailable, so this is the deterministic "
            "investigation result.",
            f"Transaction {investigation.transaction_id} is "
            f"{investigation.status.value}.",
            f"Root cause: {investigation.root_cause}.",
            f"Investigation confidence: "
            f"{investigation.investigation_confidence} out of 100.",
        ]

        if investigation.exceptions:
            parts.append("Exceptions: " + "; ".join(investigation.exceptions) + ".")

        parts.append(f"Recommended action: {investigation.recommended_action}")
        return " ".join(parts)

    def _fallback(
        self, investigation: InvestigationResponse, reason: str
    ) -> ExplanationResponse:
        return ExplanationResponse(
            transaction_id=investigation.transaction_id,
            explanation=self.build_fallback_text(investigation),
            source=ExplanationSource.FALLBACK,
            ai_available=False,
            model=None,
            notice=f"AI explanation is unavailable: {reason}.",
            investigation=investigation,
        )

    # --- Entry point --------------------------------------------------------

    def explain(self, investigation: InvestigationResponse) -> ExplanationResponse:
        """Explain an investigation result, falling back honestly on failure."""
        if not self.settings.AI_ENABLED:
            return self._fallback(investigation, "the AI layer is disabled")

        if not self.settings.AI_API_KEY.strip():
            return self._fallback(investigation, "AI_API_KEY is not configured")

        facts = self.build_facts(investigation)

        try:
            text = self._request_explanation(facts)
        except AIExplanationError as error:
            logger.warning(
                "AI explanation unavailable for %s: %s",
                investigation.transaction_id,
                error,
            )
            return self._fallback(investigation, str(error))

        invented = self.unsupported_numbers(text, facts)
        if invented:
            logger.warning(
                "Rejected AI explanation for %s: unsupported values %s",
                investigation.transaction_id,
                invented,
            )
            return self._fallback(
                investigation,
                "the generated explanation referred to details that are not "
                "in the evidence and was rejected",
            )

        return ExplanationResponse(
            transaction_id=investigation.transaction_id,
            explanation=text,
            source=ExplanationSource.AI,
            ai_available=True,
            model=self.settings.AI_MODEL,
            notice=None,
            investigation=investigation,
        )


# Shared instance, mirroring the data loader's pattern.
ai_explanation_service = AIExplanationService()


def get_ai_explanation_service() -> AIExplanationService:
    """FastAPI dependency, overridable in tests."""
    return ai_explanation_service
