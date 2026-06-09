import json
import os
import re
from typing import Dict, Any

from dotenv import load_dotenv
from vertexai import init
from vertexai.generative_models import GenerativeModel

load_dotenv(override=True)

PROJECT_ID = os.getenv("PROJECT_ID", "api-project-503305938314")
LOCATION = os.getenv("LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")


class LLMProvider:
    def ask(self, prompt: str) -> str:
        raise NotImplementedError

    def generate_explanation(self, prompt: str) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _extract_json(raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()

        text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:

            if not text.strip().endswith("}"):
                    raise ValueError("LLM response truncated before closing JSON object.")
                    

            return json.loads(text)
        

        except Exception:
            start = text.find("{")
            end = text.rfind("}")

            if start >= 0 and end > start:
                extracted = text[start : end + 1]

                print("\n=========== EXTRACTED JSON BLOCK ===========\n")
                print(extracted)
                print("\n============================================\n")

                return json.loads(extracted)

            raise

    @staticmethod
    def _retry_prompt(prompt: str) -> str:
        return (
            prompt
            + "\n\nCRITICAL JSON REPAIR INSTRUCTIONS:"
            + "\nReturn ONLY valid compact JSON."
            + "\nDo not include markdown, code fences, explanations, or commentary."
            + "\nUse exactly these top-level keys:"
            + '\n["ai_decision","confidence","risk_flag","recommended_action","explanation_summary","rule_analysis"]'
            + "\nai_decision must be one of AUTO_MERGE, APPROVE_MERGE, REVIEW_REQUIRED, BLOCK_MERGE."
            + "\nrecommended_action must be one of AUTO_MERGE, APPROVE_MERGE, REVIEW_REQUIRED, BLOCK_MERGE."
            + "\nrisk_flag must be one of LOW, MEDIUM, HIGH."
            + "\nconfidence must be a number between 0 and 1."
            + "\nexplanation_summary must be under 30 words."
            + "\nrule_analysis must be an array with no more than 3 items."
            + "\nEach rule_analysis item must contain rule, impact, and reason."
            + "\nimpact must be HIGH, MEDIUM, or LOW."
            + "\nEach reason must be under 18 words."
            + "\nUse double quotes for all strings."
            + "\nDo not use trailing commas."
            + "\nAlways fully close all JSON strings and brackets."
            + "\nNever truncate output."
            + "\nReturn complete valid JSON only."
        )


class GeminiProvider(LLMProvider):

    def __init__(self):
        init(project=PROJECT_ID, location=LOCATION)
        self.model = GenerativeModel(GEMINI_MODEL)

    def ask(self, prompt: str) -> str:
        resp = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.15,
                "max_output_tokens": 2000,
            },
        )

        return resp.text

    def generate_explanation(self, prompt: str) -> Dict[str, Any]:

        raw = self.ask(prompt)

        print("\n=========== RAW GEMINI RESPONSE ===========\n")
        print(raw)
        print("\n===========================================\n")

        try:
            cleaned = raw.replace("```json", "")
            cleaned = cleaned.replace("```", "")
            cleaned = cleaned.strip()

            print("\n=========== CLEANED GEMINI RESPONSE ===========\n")
            print(cleaned)
            print("\n================================================\n")

            return self._extract_json(cleaned)

        except Exception as e:

            print("\n=========== GEMINI PARSE FAILURE ===========\n")
            print(str(e))
            print("\nRetrying with JSON repair prompt...\n")

            raw_retry = self.ask(self._retry_prompt(prompt))

            print("\n=========== RAW GEMINI RETRY RESPONSE ===========\n")
            print(raw_retry)
            print("\n=================================================\n")

            cleaned_retry = raw_retry.replace("```json", "")
            cleaned_retry = cleaned_retry.replace("```", "")
            cleaned_retry = cleaned_retry.strip()

            return self._extract_json(cleaned_retry)


class ClaudeProvider(LLMProvider):



    def __init__(self):

        try:
            import anthropic as _anthropic

        except ImportError as e:
            raise ImportError(
                "The 'anthropic' package is required. Install it with: pip install anthropic"
            ) from e

        api_key = os.getenv("ANTHROPIC_API_KEY")
        print("CLAUDE KEY LOADED:", api_key[:20] if api_key else "MISSING")

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Add it to your environment or .env file."
            )

        self.client = _anthropic.Anthropic(api_key=api_key)

    def ask(self, prompt: str) -> str:

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

        text_parts = []

        for block in response.content:
            block_text = getattr(block, "text", None)

            if block_text:
                text_parts.append(block_text)

        return "\n".join(text_parts).strip()

    def generate_explanation(self, prompt: str) -> Dict[str, Any]:

        raw = self.ask(prompt)

        print("\n=========== RAW CLAUDE RESPONSE ===========\n")
        print(raw)
        print("\n===========================================\n")

        try:
            cleaned = raw.replace("```json", "")
            cleaned = cleaned.replace("```", "")
            cleaned = cleaned.strip()

            print("\n=========== CLEANED CLAUDE RESPONSE ===========\n")
            print(cleaned)
            print("\n================================================\n")

            return self._extract_json(cleaned)

        except Exception as e:

            print("\n=========== CLAUDE PARSE FAILURE ===========\n")
            print(str(e))
            print("\nRetrying with JSON repair prompt...\n")

            raw_retry = self.ask(self._retry_prompt(prompt))

            print("\n=========== RAW CLAUDE RETRY RESPONSE ===========\n")
            print(raw_retry)
            print("\n=================================================\n")

            cleaned_retry = raw_retry.replace("```json", "")
            cleaned_retry = cleaned_retry.replace("```", "")
            cleaned_retry = cleaned_retry.strip()

            return self._extract_json(cleaned_retry)


def get_llm_provider(provider: str = "claude") -> LLMProvider:

    provider_name = provider.lower().strip()

    if provider_name == "claude":
        return ClaudeProvider()

    if provider_name == "gemini":
        return GeminiProvider()

    raise ValueError(f"Unsupported provider: {provider}")


class LLMService:

    def __init__(self, provider: str = "claude"):
        self.provider_name = provider.lower().strip()
        self.provider = get_llm_provider(self.provider_name)

    def ask(self, prompt: str) -> str:
        return self.provider.ask(prompt)

    def generate_explanation(self, prompt: str) -> Dict[str, Any]:
        return self.provider.generate_explanation(prompt)