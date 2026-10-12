"""
LLM-based extraction of structured data from listing descriptions.

Pulls year, hours, condition, issues, included items from free-text
descriptions. Uses Anthropic's Claude API via the ANTHROPIC_API_KEY
env var. Falls back to regex-based extraction when no API key.

The regex path is a limited best-effort for the common cases:
  - "year: 2015" or "2015 Honda" or "'15"
  - "~50 hours" or "30 hrs" or "100 km"
  - "needs rings and chain"
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field


@dataclass
class ExtractedFacts:
    year: int | None = None
    hours: int | None = None
    kilometers: int | None = None
    condition: str | None = None  # excellent | good | fair | poor | project
    runs: bool | None = None
    needs: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
HOURS_RE = re.compile(r"(\d{1,4})\s*(?:hours?|hrs?)\b", re.I)
KM_RE = re.compile(r"(\d{1,6})\s*(?:km|kms|kilometers?)\b", re.I)
NEEDS_RE = re.compile(r"needs?\s+([a-z, &/]+?)(?:\.|,|$)", re.I)
RUNS_POSITIVE = re.compile(
    r"\b(runs great|starts first kick|fires right up|runs well|runs good|bike starts)\b", re.I
)
RUNS_NEGATIVE = re.compile(
    r"\b(doesn'?t run|does not run|won'?t start|not running|non running|non.runner)\b", re.I
)


def extract_regex(title: str, description: str) -> ExtractedFacts:
    """Best-effort structured extraction without an LLM."""
    text = f"{title or ''} {description or ''}"
    facts = ExtractedFacts()

    year_match = YEAR_RE.search(title or "") or YEAR_RE.search(text)
    if year_match:
        facts.year = int(year_match.group(1))

    hours_match = HOURS_RE.search(text)
    if hours_match:
        facts.hours = int(hours_match.group(1))

    km_match = KM_RE.search(text)
    if km_match:
        facts.kilometers = int(km_match.group(1))

    if RUNS_POSITIVE.search(text):
        facts.runs = True
    elif RUNS_NEGATIVE.search(text):
        facts.runs = False

    for needs_match in NEEDS_RE.finditer(text):
        parts = re.split(r"\s*(?:and|&|,|/)\s*", needs_match.group(1))
        for p in parts:
            p = p.strip()
            if p and len(p) < 40:
                facts.needs.append(p)

    return facts


def extract_llm(
    title: str, description: str, model: str = "claude-opus-4-7"
) -> ExtractedFacts | None:
    """
    Extract structured facts using Claude API.
    Returns None if no API key set or API call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        print("anthropic SDK not installed. Run: pip install anthropic")  # noqa: T201
        return None

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Extract structured facts from this dirt bike listing. Return ONLY valid JSON.

Title: {title}
Description: {description or "(none)"}

Return this JSON schema (use null for unknown):
{{
  "year": <int or null>,
  "hours": <int or null>,
  "kilometers": <int or null>,
  "condition": <"excellent"|"good"|"fair"|"poor"|"project" or null>,
  "runs": <true|false|null>,
  "needs": [<short strings describing repairs needed>],
  "includes": [<short strings describing extras included>]
}}"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Pull the JSON block out of the response
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        # LLM may return null for lists; default_factory only covers omitted keys
        data["needs"] = data.get("needs") or []
        data["includes"] = data.get("includes") or []
        return ExtractedFacts(**data)
    except Exception as exc:  # noqa: BLE001
        print(f"LLM extraction failed: {exc}")  # noqa: T201
        return None


def extract(title: str, description: str, prefer_llm: bool = False) -> ExtractedFacts:
    """Extract facts, using LLM if available and preferred, else regex."""
    if prefer_llm:
        llm_result = extract_llm(title, description)
        if llm_result is not None:
            return llm_result
    return extract_regex(title, description)
