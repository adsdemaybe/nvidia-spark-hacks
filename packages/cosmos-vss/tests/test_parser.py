from __future__ import annotations

import json
from pathlib import Path

from cosmos_vss.parser import ParseError, parse_semantic_response
from cosmos_vss.schemas import LlmSemanticPayload

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "simple_pick_place.expected.json"
VALID_JSON_TEXT = FIXTURE_PATH.read_text(encoding="utf-8")


def test_valid_plain_json():
    result = parse_semantic_response(VALID_JSON_TEXT)
    assert isinstance(result, LlmSemanticPayload)
    assert result.task_type == "pick_and_place"


def test_json_inside_markdown_fence():
    wrapped = f"Here is the analysis:\n```json\n{VALID_JSON_TEXT}\n```\n"
    result = parse_semantic_response(wrapped)
    assert isinstance(result, LlmSemanticPayload)


def test_json_inside_bare_fence():
    wrapped = f"```\n{VALID_JSON_TEXT}\n```"
    result = parse_semantic_response(wrapped)
    assert isinstance(result, LlmSemanticPayload)


def test_extra_text_before_json():
    prefixed = f"Sure, here's the structured result you asked for:\n\n{VALID_JSON_TEXT}"
    result = parse_semantic_response(prefixed)
    assert isinstance(result, LlmSemanticPayload)


def test_truncated_json_returns_parse_error():
    truncated = VALID_JSON_TEXT[: len(VALID_JSON_TEXT) // 2]
    result = parse_semantic_response(truncated)
    assert isinstance(result, ParseError)


def test_unknown_phase_returns_parse_error():
    payload = json.loads(VALID_JSON_TEXT)
    payload["timeline"][0]["phase"] = "teleport"
    result = parse_semantic_response(json.dumps(payload))
    assert isinstance(result, ParseError)
    assert result.stage == "schema_validation"


def test_missing_objects_returns_parse_error():
    payload = json.loads(VALID_JSON_TEXT)
    del payload["objects"]
    result = parse_semantic_response(json.dumps(payload))
    assert isinstance(result, ParseError)
    assert result.stage == "schema_validation"


def test_no_json_object_returns_parse_error():
    result = parse_semantic_response("I could not analyze this video.")
    assert isinstance(result, ParseError)
    assert result.stage == "locate_json"


def test_parse_error_preserves_raw_text():
    raw = "not json at all { still not json"
    result = parse_semantic_response(raw)
    assert isinstance(result, ParseError)
    assert result.raw_text == raw
