from dirtbike_tracker.infrastructure.extraction import ExtractedFacts, extract_regex


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
