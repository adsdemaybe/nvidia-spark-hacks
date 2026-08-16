"""The one interface the rest of the app depends on: `VideoSemanticAnalyzer`.

Three backends implement it — `VssAnalyzer` (primary), `CosmosNimAnalyzer`
(fallback/debug), `MockAnalyzer` (mandatory for CI, no GPU/NGC/VSS/internet
required). `build_analyzer` picks one from `Config` and never silently falls
back between them (COSMOS_VSS.md §5, §22): a backend that isn't ready raises
`AnalyzerError`, it does not switch backends on your behalf.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .config import Config
from .cosmos_client import CosmosNimClient
from .parser import ParseError, parse_semantic_response
from .prompts import build_user_prompt, SYSTEM_PROMPT
from .schemas import SemanticEpisode
from .vss_client import VssClient


class AnalyzerError(RuntimeError):
    """Raised instead of silently degrading — see COSMOS_VSS.md §22."""

    def __init__(self, *, status: str, backend: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.backend = backend
        self.reason = reason

    def to_dict(self) -> dict:
        return {"status": self.status, "backend": self.backend, "reason": self.reason}


@dataclass(frozen=True)
class VideoSource:
    path: Path | None
    url: str | None
    file_id: str | None
    source_type: Literal["file", "url", "rtsp"]
    name: str


def source_from_file(path: Path, *, name: str | None = None) -> VideoSource:
    return VideoSource(path=path, url=None, file_id=None, source_type="file", name=name or path.name)


def source_from_url(url: str, *, local_path: Path | None = None) -> VideoSource:
    return VideoSource(path=local_path, url=url, file_id=None, source_type="url", name=url)


def source_from_vss_file_id(file_id: str, *, name: str | None = None) -> VideoSource:
    return VideoSource(path=None, url=None, file_id=file_id, source_type="file", name=name or file_id)


@dataclass(frozen=True)
class AnalysisResult:
    """Richer than the `analyze()` protocol return: carries what's needed for
    artifact provenance (raw text, exact request) without bolting private
    attributes onto the pydantic `SemanticEpisode`."""

    episode: SemanticEpisode
    raw_text: str
    request_payload: dict


class VideoSemanticAnalyzer(Protocol):
    def analyze(self, source: VideoSource, *, episode_id: str | None = None) -> SemanticEpisode: ...


def _build_episode(
    payload,
    *,
    source: VideoSource,
    backend: str,
    model: str,
    episode_id: str | None,
) -> SemanticEpisode:
    return SemanticEpisode(
        **payload.model_dump(),
        episode_id=episode_id,
        source_name=source.name,
        source_type=source.source_type,
        backend=backend,
        model=model,
    )


class VssAnalyzer:
    def __init__(self, client: VssClient, *, model: str) -> None:
        self._client = client
        self._model = model

    def analyze_full(self, source: VideoSource, *, episode_id: str | None = None) -> AnalysisResult:
        if not self._client.health_ready():
            raise AnalyzerError(status="error", backend="vss", reason="configured VSS backend is not ready")

        file_id = source.file_id
        if file_id is None and source.path is not None:
            file_id = self._client.upload_file(source.path)
        if file_id is None:
            raise AnalyzerError(status="error", backend="vss", reason="video source has no local path or registered file_id")

        request_payload = {
            "model": self._model,
            "file_id": file_id,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": build_user_prompt(),
        }
        raw = self._client.generate_captions(
            file_id=file_id,
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(),
        )
        raw_text = (
            raw.get("result")
            or raw.get("caption")
            or (raw.get("choices", [{}])[0].get("message", {}).get("content") if raw.get("choices") else None)
            or json.dumps(raw)
        )

        parsed = parse_semantic_response(raw_text)
        if isinstance(parsed, ParseError):
            raise AnalyzerError(
                status="error",
                backend="vss",
                reason=f"semantic response failed schema validation: {parsed.stage}: {parsed.message}",
            )

        episode = _build_episode(parsed, source=source, backend="vss", model=self._model, episode_id=episode_id)
        return AnalysisResult(episode=episode, raw_text=raw_text, request_payload=request_payload)

    def analyze(self, source: VideoSource, *, episode_id: str | None = None) -> SemanticEpisode:
        return self.analyze_full(source, episode_id=episode_id).episode


class CosmosNimAnalyzer:
    def __init__(self, client: CosmosNimClient, *, model: str) -> None:
        self._client = client
        self._model = model

    def analyze_full(self, source: VideoSource, *, episode_id: str | None = None) -> AnalysisResult:
        if not self._client.health_ready():
            raise AnalyzerError(status="error", backend="cosmos", reason="configured Cosmos backend is not ready")
        if source.path is None:
            raise AnalyzerError(status="error", backend="cosmos", reason="direct Cosmos NIM backend requires a local file path")

        request_payload = {
            "model": self._model,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": build_user_prompt(),
            "source_name": source.name,
        }
        raw_text = self._client.chat_completion(
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(),
            video_path=source.path,
        )

        parsed = parse_semantic_response(raw_text)
        if isinstance(parsed, ParseError):
            raise AnalyzerError(
                status="error",
                backend="cosmos",
                reason=f"semantic response failed schema validation: {parsed.stage}: {parsed.message}",
            )

        episode = _build_episode(parsed, source=source, backend="cosmos", model=self._model, episode_id=episode_id)
        return AnalysisResult(episode=episode, raw_text=raw_text, request_payload=request_payload)

    def analyze(self, source: VideoSource, *, episode_id: str | None = None) -> SemanticEpisode:
        return self.analyze_full(source, episode_id=episode_id).episode


_DEFAULT_MOCK_PAYLOAD = {
    "task_type": "pick_and_place",
    "instruction": "Pick up the red bottle and place it into the right basket.",
    "summary": "A hand approaches a red bottle, grasps it, lifts it, transports it to a basket on the right, and releases it.",
    "objects": [
        {"id": "bottle_1", "label": "red bottle", "role": "manipulated_object", "attributes": {"color": "red"}},
        {"id": "basket_1", "label": "basket", "role": "target", "attributes": {"position": "right"}},
    ],
    "timeline": [
        {"start_s": 0.0, "end_s": 1.2, "phase": "approach", "description": "Hand approaches the bottle.", "object_ids": ["bottle_1"]},
        {"start_s": 1.2, "end_s": 2.0, "phase": "grasp", "description": "Hand closes around the bottle.", "object_ids": ["bottle_1"]},
        {"start_s": 2.0, "end_s": 3.1, "phase": "lift", "description": "Bottle is lifted off the surface.", "object_ids": ["bottle_1"]},
        {"start_s": 3.1, "end_s": 5.8, "phase": "transport", "description": "Bottle is carried toward the basket.", "object_ids": ["bottle_1", "basket_1"]},
        {"start_s": 5.8, "end_s": 6.4, "phase": "release", "description": "Hand opens and releases the bottle into the basket.", "object_ids": ["bottle_1", "basket_1"]},
    ],
    "spatial_relations": [
        {"time_s": 6.4, "subject_id": "bottle_1", "relation": "inside", "object_id": "basket_1"},
    ],
    "success_condition": "red bottle is inside the target basket",
    "ambiguity_notes": [],
}


class MockAnalyzer:
    """Deterministic, offline analyzer. Mandatory for CI (COSMOS_VSS.md §10)."""

    def __init__(self, *, model: str = "mock-cosmos-reason", fixture: dict | None = None) -> None:
        self._model = model
        self._fixture = fixture if fixture is not None else _DEFAULT_MOCK_PAYLOAD

    def analyze_full(self, source: VideoSource, *, episode_id: str | None = None) -> AnalysisResult:
        raw_text = json.dumps(self._fixture)
        parsed = parse_semantic_response(raw_text)
        if isinstance(parsed, ParseError):
            raise AnalyzerError(status="error", backend="mock", reason=f"mock fixture failed validation: {parsed.message}")

        episode = _build_episode(parsed, source=source, backend="mock", model=self._model, episode_id=episode_id)
        return AnalysisResult(episode=episode, raw_text=raw_text, request_payload={"mock": True, "source_name": source.name})

    def analyze(self, source: VideoSource, *, episode_id: str | None = None) -> SemanticEpisode:
        return self.analyze_full(source, episode_id=episode_id).episode


def build_analyzer(config: Config):
    if config.backend == "vss":
        client = VssClient(config.vss_base_url, timeout_s=config.timeout_s)
        return VssAnalyzer(client, model=config.vss_model)
    if config.backend == "cosmos":
        client = CosmosNimClient(config.cosmos_base_url, timeout_s=config.timeout_s)
        return CosmosNimAnalyzer(client, model=config.cosmos_model)
    if config.backend == "mock":
        return MockAnalyzer()
    raise ValueError(f"unknown backend: {config.backend}")
