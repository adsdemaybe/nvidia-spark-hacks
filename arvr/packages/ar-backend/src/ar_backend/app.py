"""FastAPI app factory — spec section 36-40 API surface.

    uv run uvicorn ar_backend.app:app --reload --port 8000

Episodes, Scenes, Twin (live physics via ar_sim), and Corrections all on
one port — a client only needs one base URL. Follow (section 39) isn't
wired in yet; `ar_contracts.FollowSession` exists and is tested, adding
the session/WS endpoints is a reasonable next step once a client needs
them.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .corrections import build_router as build_corrections_router
from .episodes import build_router as build_episodes_router
from .scenes import build_router as build_scenes_router
from .store import CorrectionStore, EpisodeStore
from .twin import build_router as build_twin_router

ARVR_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SCENES_DIR = ARVR_ROOT / "fixtures" / "ar-xr"
DEFAULT_DATASET_ROOT = ARVR_ROOT / "data" / "lerobot"


def create_app(
    *,
    scenes_dir: Path | None = None,
    dataset_root: Path | None = None,
    twin_hz: float = 30.0,
) -> FastAPI:
    scenes_dir = scenes_dir or DEFAULT_SCENES_DIR
    dataset_root = dataset_root or DEFAULT_DATASET_ROOT

    app = FastAPI(title="struct-ar-api", version="0.1.0")
    episode_store = EpisodeStore()
    correction_store = CorrectionStore()
    app.include_router(build_episodes_router(episode_store, dataset_root))
    app.include_router(build_scenes_router(scenes_dir))
    app.include_router(build_twin_router(twin_hz))
    app.include_router(build_corrections_router(correction_store))
    return app


# `uvicorn ar_backend.app:app` needs a module-level instance. Dataset root is
# overridable via env var so a Spark deployment can point it at
# ar-vr/sky/artifacts/ instead of the repo tree, per spec section 87 step 10.
app = create_app(
    dataset_root=Path(os.environ["ARVR_DATASET_ROOT"])
    if "ARVR_DATASET_ROOT" in os.environ
    else None
)
