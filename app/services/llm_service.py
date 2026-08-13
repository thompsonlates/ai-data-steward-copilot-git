from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict

from dotenv import load_dotenv
from vertexai import init
from vertexai.generative_models import GenerativeModel


load_dotenv(override=True)

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv(
    "PROJECT_ID",
    "api-project-503305938314",
)
LOCATION = os.getenv(
    "LOCATION",
    "us-central1",
)
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash",
)
CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL",
    "claude-sonnet-4-6",
)


class LLMProvider:
    def ask(
        self,
        prompt: str,
    ) -> str:
        raise NotImplementedError

    def generate_explanation(
        self,
        prompt: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _extract_json(
        raw: str,
    ) -> Dict[str, Any]:
        text = (raw or "").strip()

        text = re.sub(
            r"^```json\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"^```\s*",
            "",
            text,
        )
        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

        if not text:
            raise ValueError(
                "LLM returned an empty response."
            )

        try:
            if not text.endswith("}"):
                raise ValueError(
                    "LLM response truncated before "
                    "closing JSON object."
                )

            parsed = json.loads(text)

            if not isinstance(parsed, dict):
                raise ValueError(
                    "LLM response was not a JSON object."
                )

            return parsed

        except (json.JSONDecodeError, ValueError):
            start = text.find("{")
            end = text.rfind("}")

            if start < 0 or end <= start:
                raise

            extracted = text[
                start : end + 1
            ]

            parsed = json.loads(extracted)

            if not isinstance(parsed, dict):
                raise ValueError(
                    "Extracted LLM response was not "
                    "a JSON object."
                )

            return parsed

    @staticmethod
    def _retry_prompt(
        prompt: str,
    ) -> str:
        return (
            prompt
            + "\n\nCRITICAL JSON REPAIR INSTRUCTIONS:"
            + "\nReturn ONLY valid compact JSON."
            + "\nDo not include markdown, code fences, "
            "explanations, or commentary."
            + "\nUse exactly these top-level keys:"
            + '\n["ai_decision","confidence","risk_flag",'
            '"recommended_action","explanation_summary",'
            '"rule_analysis"]'
            + "\nai_decision must be one of AUTO_MERGE, "
            "APPROVE_MERGE, REVIEW_REQUIRED, BLOCK_MERGE."
            + "\nrecommended_action must be one of "
            "AUTO_MERGE, APPROVE_MERGE, REVIEW_REQUIRED, "
            "BLOCK_MERGE."
            + "\nrisk_flag must be one of LOW, MEDIUM, HIGH."
            + "\nconfidence must be a number between 0 and 1."
            + "\nexplanation_summary must be under 30 words."
            + "\nrule_analysis must be an array with no more "
            "than 3 items."
            + "\nEach rule_analysis item must contain rule, "
            "impact, and reason."
            + "\nimpact must be HIGH, MEDIUM, or LOW."
            + "\nEach reason must be under 18 words."
            + "\nUse double quotes for all strings."
            + "\nDo not use trailing commas."
            + "\nAlways fully close all JSON strings and brackets."
            + "\nNever truncate output."
            + "\nReturn complete valid JSON only."
        )


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        init(
            project=PROJECT_ID,
            location=LOCATION,
        )
        self.model = GenerativeModel(
            GEMINI_MODEL
        )

    def ask(
        self,
        prompt: str,
    ) -> str:
        response = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.15,
                "max_output_tokens": 2000,
            },
        )

        return response.text

    def generate_explanation(
        self,
        prompt: str,
    ) -> Dict[str, Any]:
        raw = self.ask(prompt)

        try:
            return self._extract_json(raw)

        except Exception as exc:
            logger.warning(
                "Gemini response parsing failed; "
                "retrying JSON repair. error=%s",
                type(exc).__name__,
            )

            raw_retry = self.ask(
                self._retry_prompt(prompt)
            )

            return self._extract_json(
                raw_retry
            )


class ClaudeProvider(LLMProvider):
    def __init__(self) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required. "
                "Install it with: pip install anthropic"
            ) from exc

        api_key = (
            os.getenv("ANTHROPIC_API_KEY")
            or ""
        ).strip()

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your environment or .env file."
            )

        self.client = _anthropic.Anthropic(
            api_key=api_key
        )

    def ask(
        self,
        prompt: str,
    ) -> str:
        response = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            temperature=0.15,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        text_parts: list[str] = []

        for block in response.content:
            block_text = getattr(
                block,
                "text",
                None,
            )

            if block_text:
                text_parts.append(
                    block_text
                )

        return "\n".join(
            text_parts
        ).strip()

    def generate_explanation(
        self,
        prompt: str,
    ) -> Dict[str, Any]:
        raw = self.ask(prompt)

        try:
            return self._extract_json(raw)

        except Exception as exc:
            logger.warning(
                "Claude response parsing failed; "
                "retrying JSON repair. error=%s",
                type(exc).__name__,
            )

            raw_retry = self.ask(
                self._retry_prompt(prompt)
            )

            return self._extract_json(
                raw_retry
            )


def get_llm_provider(
    provider: str = "claude",
) -> LLMProvider:
    provider_name = str(
        provider or ""
    ).lower().strip()

    if provider_name == "claude":
        return ClaudeProvider()

    if provider_name == "gemini":
        return GeminiProvider()

    raise ValueError(
        f"Unsupported provider: {provider}"
    )


class LLMService:
    """
    Tenant-bound LLM invocation facade.

    Providers themselves remain stateless and tenant-agnostic. Tenant
    isolation is enforced here so tenant-sensitive AI calls cannot execute
    without an authenticated organization context.

    organization_id is used only as an application security boundary and
    is not automatically inserted into the prompt sent to the model.
    """

    def __init__(
        self,
        provider: str = "claude",
    ) -> None:
        self.provider_name = str(
            provider or ""
        ).lower().strip()

        self.provider = get_llm_provider(
            self.provider_name
        )

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated LLM operations."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the "
                "org_ identifier standard."
            )

        return normalized

    def ask(
        self,
        prompt: str,
        *,
        organization_id: str,
    ) -> str:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        if not str(prompt or "").strip():
            raise ValueError(
                "LLM prompt cannot be empty."
            )

        logger.debug(
            "LLM request started. provider=%s "
            "organization_id=%s",
            self.provider_name,
            effective_organization_id,
        )

        response = self.provider.ask(
            prompt
        )

        logger.debug(
            "LLM request completed. provider=%s "
            "organization_id=%s",
            self.provider_name,
            effective_organization_id,
        )

        return response

    def generate_explanation(
        self,
        prompt: str,
        *,
        organization_id: str,
    ) -> Dict[str, Any]:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        if not str(prompt or "").strip():
            raise ValueError(
                "LLM prompt cannot be empty."
            )

        logger.debug(
            "LLM explanation request started. "
            "provider=%s organization_id=%s",
            self.provider_name,
            effective_organization_id,
        )

        result = (
            self.provider.generate_explanation(
                prompt
            )
        )

        logger.debug(
            "LLM explanation request completed. "
            "provider=%s organization_id=%s",
            self.provider_name,
            effective_organization_id,
        )

        return result
