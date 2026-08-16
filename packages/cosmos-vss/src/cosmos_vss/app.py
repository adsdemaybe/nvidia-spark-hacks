"""Standalone FastAPI sidecar. Runs on port 8100 by default — the NVIDIA
VSS/Cosmos service it talks to runs on 8000 (COSMOS_VSS.md §11).

Config and the analyzer are built once at import time from the environment,
same as any other stateless service process; tests override the environment
before importing this module (see tests/test_api.py).
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .analyzer import (
    AnalysisResult,
    AnalyzerError,
    build_analyzer,
    source_from_file,
    source_from_vss_file_id,
    VideoSource,
)
from .artifacts import Provenance, load_artifact, sha256_bytes, write_artifact
from .config import Config
from .prompts import PROMPT_VERSION
from .schemas import SemanticEpisode

logger = logging.getLogger("cosmos_vss")

config = Config.from_env()
analyzer = build_analyzer(config)

app = FastAPI(title="cosmos-vss-sidecar")

_STATIC_DIR = Path(__file__).parent / "static"


class UrlAnalyzeRequest(BaseModel):
    url: str
    episode_id: str | None = None


class VssAssetAnalyzeRequest(BaseModel):
    file_id: str
    episode_id: str | None = None


def _backend_ready() -> bool:
    try:
        if config.backend == "vss":
            from .vss_client import VssClient

            return VssClient(config.vss_base_url, timeout_s=10.0).health_ready()
        if config.backend == "cosmos":
            from .cosmos_client import CosmosNimClient

            return CosmosNimClient(config.cosmos_base_url, timeout_s=10.0).health_ready()
        return True  # mock backend is always "ready"
    except Exception:  # defensive: health check must never 500 the endpoint
        return False


@app.get("/health")
def health() -> dict:
    ready = _backend_ready()
    return {
        "service": "cosmos-vss-sidecar",
        "status": "ok" if ready else "degraded",
        "backend": config.backend,
        "backend_ready": ready,
        "model": config.active_model,
    }


@app.get("/", response_class=HTMLResponse)
def debug_ui() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _persist(result: AnalysisResult, *, request_extra: dict) -> SemanticEpisode:
    provenance = Provenance(
        backend=result.episode.backend,
        model=result.episode.model,
        source_sha256=request_extra.get("source_sha256", ""),
        source_name=result.episode.source_name,
        episode_id=result.episode.episode_id,
        prompt_version=PROMPT_VERSION,
        schema_version=result.episode.schema_version,
    )
    write_artifact(
        config.artifact_dir,
        result.episode,
        raw_response=result.raw_text,
        request_payload={**result.request_payload, **request_extra},
        provenance=provenance,
    )
    return result.episode


def _run_analysis(source: VideoSource, *, episode_id: str | None, source_sha256: str) -> SemanticEpisode:
    start = time.monotonic()
    try:
        result = analyzer.analyze_full(source, episode_id=episode_id)
    except AnalyzerError as exc:
        logger.info(
            "episode_id=%s backend=%s model=%s parse_success=false reason=%s",
            episode_id, exc.backend, config.active_model, exc.reason,
        )
        raise HTTPException(status_code=502, detail=exc.to_dict()) from exc

    episode = _persist(result, request_extra={"source_sha256": source_sha256})
    latency = time.monotonic() - start
    artifact_path = config.artifact_dir / episode.semantic_id / "semantic.json"
    logger.info(
        "semantic_id=%s episode_id=%s backend=%s model=%s source_hash=%s latency=%.3f parse_success=true artifact=%s",
        episode.semantic_id, episode_id, episode.backend, episode.model, source_sha256[:12], latency, artifact_path,
    )
    return episode


@app.post("/analyze/video", response_model=SemanticEpisode)
async def analyze_video(file: UploadFile = File(...), episode_id: str | None = Form(default=None)) -> SemanticEpisode:
    data = await file.read()
    source_hash = sha256_bytes(data)
    suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        source = source_from_file(tmp_path, name=file.filename or tmp_path.name)
        return _run_analysis(source, episode_id=episode_id, source_sha256=source_hash)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/analyze/url", response_model=SemanticEpisode)
async def analyze_url(req: UrlAnalyzeRequest) -> SemanticEpisode:
    try:
        async with httpx.AsyncClient(timeout=config.timeout_s) as client:
            resp = await client.get(req.url)
            resp.raise_for_status()
            data = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={"status": "error", "backend": config.backend, "reason": f"failed to fetch url: {exc}"},
        ) from exc

    source_hash = sha256_bytes(data)
    suffix = Path(req.url).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        source = VideoSource(path=tmp_path, url=req.url, file_id=None, source_type="url", name=req.url)
        return _run_analysis(source, episode_id=req.episode_id, source_sha256=source_hash)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/analyze/vss", response_model=SemanticEpisode)
async def analyze_vss_asset(req: VssAssetAnalyzeRequest) -> SemanticEpisode:
    if config.backend != "vss":
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "backend": config.backend, "reason": "this endpoint requires COSMOS_VSS_BACKEND=vss"},
        )
    source = source_from_vss_file_id(req.file_id)
    return _run_analysis(source, episode_id=req.episode_id, source_sha256="")


@app.get("/semantic/{semantic_id}", response_model=SemanticEpisode)
def get_semantic(semantic_id: str) -> SemanticEpisode:
    try:
        return load_artifact(config.artifact_dir, semantic_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"semantic artifact not found: {semantic_id}") from exc
