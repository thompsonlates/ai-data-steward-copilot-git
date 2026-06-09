import json

from vertexai import init
from vertexai.generative_models import GenerativeModel

PROJECT_ID = "api-project-503305938314"
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash"


class GeminiService:
    def __init__(self):
        init(project=PROJECT_ID, location=LOCATION)
        self.model = GenerativeModel(MODEL_NAME)

    def ask(self, prompt: str) -> str:
        resp = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 2048,
                "response_mime_type": "application/json",
            },
        )
        return resp.text

    def generate_explanation(self, prompt: str) -> dict:
        """
        Generates structured JSON for match explain use cases.
        Retries once with stricter instructions if parsing fails.
        """
        raw = self.ask(prompt)

        try:
            return json.loads(raw)
        except Exception:
            retry_prompt = (
                prompt
                + "\n\nIMPORTANT: Return ONLY compact valid JSON."
                + "\nDo not include markdown, code fences, or commentary."
                + "\nKeep explanation_summary under 30 words."
                + "\nReturn at most 2 rule_analysis items."
                + "\nKeep each reason under 18 words."
                + "\nUse double quotes for all strings."
                + "\nDo not use trailing commas."
            )

            raw_retry = self.ask(retry_prompt)
            return json.loads(raw_retry)
        
        print("USING GEMINI PATH")