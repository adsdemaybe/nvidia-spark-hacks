"""`cad.design_enclosure(board_report, intent)` — the §6 entry point.

Order of operations matters and is deliberate:

    build solid -> measure it -> check fit -> (optionally) score via engine.evaluate

The enclosure is *measured* into its report rather than described from the
intent that requested it. If the solid modeller produced something other than
what the intent asked for, the report says what exists, not what was wanted —
that is the difference between a report and a restatement of the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.catalogue import resolve as resolve_catalogue
from engine.evaluate import evaluate
from engine.geometry.registry import _mass_properties_from_shape, build as build_geometry

from cad_api import geometry
from cad_api.contracts import (
    Artifacts,
    BoardReport,
    Box3,
    EnclosureIntent,
    EnclosureReport,
    FitResult,
    Point2,
)
from cad_api.fit import check_fit


@dataclass(frozen=True)
class DesignResult:
    enclosure_report: EnclosureReport
    fit: FitResult
    ir: object  # engine.ir.RobotIR
    evaluation: dict | None


def design_enclosure(
    board: BoardReport,
    intent: EnclosureIntent | None = None,
    *,
    artifact_dir: Path | None = None,
    run_evaluate: bool = True,
    max_tier: int = 0,
) -> DesignResult:
    intent = intent or EnclosureIntent()

    built = geometry.build_enclosure(board, intent)
    ir = geometry.enclosure_ir(built, intent, name=intent.name)

    # Mass properties come from the engine's own measurement path, not from a
    # local calculation — one source of truth for mass (§11 non-negotiable #2:
    # mass properties are computed downstream, never authored).
    #
    # Two different solids exist here and they must not be confused. `shell_props`
    # measures the bare shell the IR describes, and is what engine.evaluate() scores,
    # because the IR's `enclosure_shell` generator builds exactly that. `built.part`
    # is what gets exported: the same shell plus standoff bosses, minus pilot holes
    # and port cutouts. Reporting the shell's mass for the exported solid understated
    # it by ~3% on a four-standoff board and grows with feature count — and it would
    # be a report describing an object nobody receives.
    shell_props = build_geometry(ir.link("shell").geometry).mass_properties
    material = resolve_catalogue("materials", intent.material)
    built_props = _mass_properties_from_shape(built.part, material.density.value)

    wall = intent.wall_thickness_mm
    report = EnclosureReport(
        cavity_mm=Box3(
            length_mm=built.cavity_l_mm,
            width_mm=built.cavity_w_mm,
            height_mm=built.cavity_h_mm,
        ),
        standoff_positions=geometry.standoffs_for(board, intent),
        port_cutouts=geometry.port_cutouts_for(board, intent),
        wall_thickness_mm=wall,
        max_component_height_mm=board.max_component_height_mm("top"),
        board_origin_mm=Point2(x_mm=built.offset_x_mm, y_mm=built.offset_y_mm),
        outer_mm=Box3(
            length_mm=built.cavity_l_mm + 2 * wall,
            width_mm=built.cavity_w_mm + 2 * wall,
            height_mm=built.cavity_h_mm + wall,
        ),
        material=intent.material,
        mass_kg=built_props.mass,
        artifacts=Artifacts(content_hash=geometry.content_hash(board, intent)),
    )

    if artifact_dir is not None:
        stem = f"{intent.name}-{report.artifacts.content_hash[:12]}"
        written = geometry.export_artifacts(built.part, artifact_dir, stem)
        report.artifacts.step_path = written.get("step", "")
        report.artifacts.glb_path = written.get("glb", "")
        report.artifacts.stl_path = written.get("stl", "")

    fit = check_fit(board, report)

    evaluation: dict | None = None
    if run_evaluate:
        rep = evaluate(ir, max_tier=max_tier, mass_properties={"shell": shell_props})
        evaluation = {
            "design_name": rep.design_name,
            "passed": rep.passed,
            "tiers_run": rep.tiers_run,
            "tiers_skipped": rep.tiers_skipped,
            "results": [
                {
                    "name": r.name,
                    "magnitude": r.magnitude,
                    "passed": r.passed,
                    "unit": r.unit,
                    "detail": r.detail,
                }
                for r in rep.results
            ],
        }

    return DesignResult(enclosure_report=report, fit=fit, ir=ir, evaluation=evaluation)
