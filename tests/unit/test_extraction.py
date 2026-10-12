import sys
from types import SimpleNamespace

import pytest

from dirtbike_tracker.infrastructure.extraction import ExtractedFacts, extract_llm, extract_regex


def test_extract_regex_pulls_facts() -> None:
    facts = extract_regex("2015 Honda CRF150F", "About 40 hours, runs great. Needs chain and tires")

    assert facts.year == 2015
    assert facts.hours == 40
    assert facts.runs is True
    assert facts.needs == ["chain", "tires"]
    assert facts.includes == []


def test_default_lists_are_not_shared() -> None:
    a, b = ExtractedFacts(), ExtractedFacts()

    a.needs.append("rings")

    assert b.needs == []


def test_extract_llm_normalizes_null_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = '{"year": 2012, "hours": null, "needs": null, "includes": null}'
    response = SimpleNamespace(content=[SimpleNamespace(text=reply)])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **_: client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    facts = extract_llm("2012 CRF150F", "")

    assert facts is not None
    assert facts.year == 2012
    assert facts.needs == []
    assert facts.includes == []


def test_needs_splits_on_whole_word_and_only() -> None:
    facts = extract_regex("", "Needs handlebars, tires and sand paper. Kneads dough")

    assert facts.needs == ["handlebars", "tires", "sand paper"]


@pytest.mark.parametrize("text", ["Non-runner", "doesnt run", "WON'T START"])
def test_runs_negative_uses_the_shared_not_running_phrases(text: str) -> None:
    assert extract_regex("CRF150F", text).runs is False
