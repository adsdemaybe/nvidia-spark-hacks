"""FastAPI app factory — spec section 36-37 API surface.

    uv run uvicorn ar_backend.app:app --reload --port 8000

Twin/Follow/Correction streaming (sections 38-40) aren't wired in here yet
— Twin has a standalone equivalent already (tools/mock_twin_server.py);
folding it into this app is a reasonable next step once a client actually
needs both REST and the twin stream from the same port.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .episodes import build_router as build_episodes_router
from .scenes import build_router as build_scenes_router
from .store import EpisodeStore

ARVR_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SCENES_DIR = ARVR_ROOT / "fixtures" / "ar-xr"
DEFAULT_DATASET_ROOT = ARVR_ROOT / "data" / "lerobot"


def create_app(
    *,
    scenes_dir: Path | None = None,
    dataset_root: Path | None = None,
) -> FastAPI:
    scenes_dir = scenes_dir or DEFAULT_SCENES_DIR
    dataset_root = dataset_root or DEFAULT_DATASET_ROOT

    app = FastAPI(title="struct-ar-api", version="0.1.0")
    store = EpisodeStore()
    app.include_router(build_episodes_router(store, dataset_root))
    app.include_router(build_scenes_router(scenes_dir))
    return app


# `uvicorn ar_backend.app:app` needs a module-level instance. Dataset root is
# overridable via env var so a Spark deployment can point it at
# ar-vr/sky/artifacts/ instead of the repo tree, per spec section 87 step 10.
app = create_app(
    dataset_root=Path(os.environ["ARVR_DATASET_ROOT"])
    if "ARVR_DATASET_ROOT" in os.environ
    else None
)
