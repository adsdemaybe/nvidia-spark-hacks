"""Turn raw model text into a validated `LlmSemanticPayload`, never a crash.

Model output is never trusted directly: markdown fences are stripped, a
balanced JSON object is located inside whatever surrounding text the model
produced, parsed, and validated against the schema. Any failure at any stage
is returned as a `ParseError` rather than raised, so callers can preserve the
raw response for debugging instead of losing it to an exception.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from .schemas import LlmSemanticPayload

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ParseError:
    stage: str
    message: str
    raw_text: str


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _locate_json_object(text: str) -> str | None:
    """Return the first balanced `{...}` span in `text`, or None."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_semantic_response(raw_text: str) -> LlmSemanticPayload | ParseError:
    stripped = _strip_fences(raw_text)
    candidate = _locate_json_object(stripped)
    if candidate is None:
        return ParseError(stage="locate_json", message="no JSON object found in response", raw_text=raw_text)

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return ParseError(stage="json_decode", message=str(exc), raw_text=raw_text)

    if not isinstance(payload, dict):
        return ParseError(stage="json_decode", message="top-level JSON value is not an object", raw_text=raw_text)

    try:
        return LlmSemanticPayload.model_validate(payload)
    except ValidationError as exc:
        return ParseError(stage="schema_validation", message=str(exc), raw_text=raw_text)
