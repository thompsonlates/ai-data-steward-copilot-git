import json
import re
from typing import Any, Dict

def parse_llm_json(text: str) -> Dict[str, Any]:
    if text is None:
        raise ValueError("LLM returned None")

    s = text.strip()
    if not s:
        raise ValueError("LLM returned empty text")

    # Strip ```json fences if present
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()

    # Extract first {...} block if extra text exists
    if not (s.startswith("{") and s.endswith("}")):
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            raise ValueError(f"LLM did not return JSON. Got: {s[:200]}")
        s = m.group(0)

    return json.loads(s)

