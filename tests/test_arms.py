"""bench.arms: a direct model answer becomes a COI JSON (or a failure) and its elements are counted, offline."""

import pytest

from bench.arms import elements, parse


def test_parse_cuts_fences_and_text_around_the_object():
    assert parse('Oto JSON:\n```json\n{"title": "A", "tabs": []}\n```') == {"title": "A", "tabs": []}


@pytest.mark.parametrize("raw", ["", "brak", '["a"]', '{"title": "A", "tabs": ['])
def test_parse_fails_on_anything_but_one_object(raw):
    with pytest.raises(ValueError):
        parse(raw)


def test_elements_counts_nested_fields_and_show_conditions():
    show = [{"type": "SHOW", "query": "x"}]
    coi = {"tabs": [
        {"questions": [{"field": {"type": "SECTION", "repeatingSectionGrid": [
            {"field": {"type": "TEXT_FIELD"}}, {"field": {"type": "RADIO", "states": show}}]}}]},
        {"questions": [{"field": {"type": "SECTION", "states": show, "repeatingSectionGrid": [
            {"field": {"type": "TEXT_BLOCK"}}]}}, "not a question"]},
    ]}  # fmt: skip
    assert elements(coi) == {"pages": 2, "inputs": 2, "show": 2,
                             "types": {"?": 1, "RADIO": 1, "SECTION": 2, "TEXT_BLOCK": 1, "TEXT_FIELD": 1}}
