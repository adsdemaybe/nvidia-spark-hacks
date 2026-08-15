"""HTTP surface tests — the shape pcb-ai actually talks to.

These go through the real FastAPI app (TestClient), so a schema change that
breaks the TypeScript client breaks a test here first.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cad_api.service import app

client = TestClient(app)

BOARD = {
    "design_id": "test-r1",
    "outline_mm": {
        "points": [
            {"x_mm": 0, "y_mm": 0},
            {"x_mm": 60, "y_mm": 0},
            {"x_mm": 60, "y_mm": 40},
            {"x_mm": 0, "y_mm": 40},
        ]
    },
    "thickness_mm": 1.6,
    "mounting_holes": [{"x_mm": 4, "y_mm": 4, "diameter_mm": 3.2}],
    "component_heightmap": [
        {"ref": "U1", "x_mm": 30, "y_mm": 20, "width_mm": 12, "depth_mm": 12, "height_mm": 2.0, "side": "top"}
    ],
    "connector_edges": [
        {"ref": "J1", "edge": "east", "x_mm": 20, "y_mm": 20, "width_mm": 9, "height_mm": 3.5, "needs_cutout": True}
    ],
    "keepouts": [],
    "thermal_hotspots": [],
}


def test_health_lists_the_enclosure_generator():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # importing cad_api must have registered the generator into the ENGINE registry
    assert "enclosure_shell" in body["generators"]


def test_materials_come_from_the_engine_catalogue():
    r = client.get("/cad/materials")
    assert r.status_code == 200
    assert "pla" in r.json()["materials"]


def test_design_enclosure_round_trip():
    r = client.post("/cad/design_enclosure", json={"board_report": BOARD, "emit_artifacts": False})
    assert r.status_code == 200, r.text
    body = r.json()

    er = body["enclosure_report"]
    assert er["cavity_mm"]["length_mm"] == pytest.approx(63.0)
    assert er["cavity_mm"]["width_mm"] == pytest.approx(43.0)
    assert len(er["standoff_positions"]) == 1
    assert len(er["port_cutouts"]) == 1
    assert er["port_cutouts"][0]["ref"] == "J1"
    assert er["mass_kg"] > 0

    assert body["fit"]["ok"] is True
    assert body["fit"]["violations"] == []

    # the enclosure came back as a RobotIR the engine can consume
    assert body["ir_json"]["links"][0]["geometry"]["generator"] == "enclosure_shell"
    assert body["evaluation"]["passed"] is True


def test_check_fit_endpoint_reports_violations():
    designed = client.post(
        "/cad/design_enclosure", json={"board_report": BOARD, "emit_artifacts": False}
    ).json()["enclosure_report"]

    huge = dict(BOARD)
    huge["outline_mm"] = {
        "points": [
            {"x_mm": 0, "y_mm": 0},
            {"x_mm": 500, "y_mm": 0},
            {"x_mm": 500, "y_mm": 400},
            {"x_mm": 0, "y_mm": 400},
        ]
    }

    r = client.post("/cad/check_fit", json={"board_report": huge, "enclosure_report": designed})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any(v["code"] == "board_exceeds_cavity" for v in body["violations"])


def test_constrain_board_endpoint():
    designed = client.post(
        "/cad/design_enclosure", json={"board_report": BOARD, "emit_artifacts": False}
    ).json()["enclosure_report"]

    r = client.post(
        "/cad/constrain_board",
        json={"reason": "board overhangs", "enclosure_report": designed},
    )
    assert r.status_code == 200
    env = r.json()
    assert env["reason"] == "board overhangs"
    assert env["max_outline_mm"]["max_x_mm"] == pytest.approx(60.0)
    assert len(env["mounting_hole_pattern"]) == 1


def test_unknown_material_is_a_422_not_a_500():
    """An agent picking a material that isn't in the catalogue is a caller error
    with a nameable cause, not an opaque server failure."""
    r = client.post(
        "/cad/design_enclosure",
        json={"board_report": BOARD, "intent": {"material": "unobtainium"}, "emit_artifacts": False},
    )
    assert r.status_code == 422
    assert "unobtainium" in r.text


def test_degenerate_outline_is_rejected_by_the_schema():
    bad = dict(BOARD)
    bad["outline_mm"] = {"points": [{"x_mm": 0, "y_mm": 0}, {"x_mm": 1, "y_mm": 1}]}  # only 2 points
    r = client.post("/cad/design_enclosure", json={"board_report": bad, "emit_artifacts": False})
    assert r.status_code == 422


def test_artifact_path_traversal_is_refused():
    r = client.get("/artifacts/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404
