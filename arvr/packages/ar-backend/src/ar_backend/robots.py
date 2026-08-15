"""Robots API — Shadow Robot Spatial Demonstration Pipeline spec section 46.

    GET /robots               enumerate available fixture robots
    GET /robots/{robot_id}    one robot's manifest

Enumerates fixtures/spatial-training/robots/*/manifest.json directly rather
than going through FixtureRobotProvider (which needs spatial_providers'
Linux-only ar_datapipe dependency chain just to construct) -- listing
manifests is pure file/JSON I/O and should work on every platform, matching
scenes.py's existing GET /scenes pattern.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

DEFAULT_ROBOTS_DIR = (
    Path(__file__).resolve().parents[4] / "fixtures" / "spatial-training" / "robots"
)


def build_router(robots_dir: Path | None = None) -> APIRouter:
    robots_dir = robots_dir or DEFAULT_ROBOTS_DIR
    router = APIRouter(prefix="/robots", tags=["robots"])

    @router.get("")
    def list_robots() -> list[dict]:
        manifests = []
        if robots_dir.exists():
            for manifest_path in sorted(robots_dir.glob("*/manifest.json")):
                manifests.append(json.loads(manifest_path.read_text()))
        return manifests

    @router.get("/{robot_id}")
    def get_robot(robot_id: str) -> dict:
        manifest_path = robots_dir / robot_id / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(404, f"unknown robot_id {robot_id!r}")
        return json.loads(manifest_path.read_text())

    return router
