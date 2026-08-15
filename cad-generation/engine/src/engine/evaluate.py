"""The harness. §11 non-negotiable #7: "The engine has zero I/O — if it
imports the ORM, the architecture is broken." Every function in this module
down to `evaluate()` is a pure RobotIR -> report transform; only the
`__main__` CLI block at the bottom touches the filesystem, exactly as
described in §6: `python -m engine.evaluate ir.json, no infrastructure`.

This is the one place that enforces the rule at the center of the whole
platform: the agent proposes, this module disposes. Nothing upstream may
declare a design valid — only `evaluate()` returns a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.criteria import CriterionResult, all_criteria
from engine.geometry.registry import build as build_geometry
from engine.ir import RobotIR
from engine.mass_properties import MassProperties


@dataclass(frozen=True)
class EvaluationReport:
    design_id: str
    design_name: str
    results: list[CriterionResult]
    tiers_run: list[int]
    tiers_skipped: list[int]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CriterionResult]:
        return [r for r in self.results if not r.passed]


def compute_mass_properties(ir: RobotIR) -> dict[str, MassProperties]:
    return {link.id: build_geometry(link.geometry).mass_properties for link in ir.links}


def evaluate(
    ir: RobotIR,
    *,
    max_tier: int = 0,
    mass_properties: dict[str, MassProperties] | None = None,
) -> EvaluationReport:
    """Pure function: RobotIR -> EvaluationReport. No I/O.

    `max_tier` bounds which criteria run, per the tier table in §3. Phase 1
    ships tier 0 only; tiers 1-3 (Pinocchio, MuJoCo, Drake) are follow-up
    work and will register into the same criteria registry once wired up —
    this function does not change when they land.
    """
    if mass_properties is None:
        mass_properties = compute_mass_properties(ir)

    criteria = all_criteria()
    tiers_run: set[int] = set()
    tiers_skipped: set[int] = set()
    results: list[CriterionResult] = []

    for criterion in criteria:
        if criterion.tier > max_tier:
            tiers_skipped.add(criterion.tier)
            continue
        tiers_run.add(criterion.tier)
        results.extend(criterion.fn(ir, mass_properties))

    return EvaluationReport(
        design_id=str(ir.id),
        design_name=ir.name,
        results=results,
        tiers_run=sorted(tiers_run),
        tiers_skipped=sorted(tiers_skipped),
    )


def _load_ir(path: str) -> RobotIR:
    import json

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return RobotIR.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m engine.evaluate")
    parser.add_argument("ir_path", help="path to a RobotIR JSON file")
    parser.add_argument("--max-tier", type=int, default=0, help="highest criteria tier to run (default: 0)")
    args = parser.parse_args(argv)

    ir = _load_ir(args.ir_path)
    report = evaluate(ir, max_tier=args.max_tier)

    print(f"design: {report.design_name} ({report.design_id})")
    print(f"tiers run: {report.tiers_run}  tiers skipped (not a pass): {report.tiers_skipped}")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name:30s} magnitude={r.magnitude:+.4f} {r.unit:14s} {r.detail}")
    print(f"overall: {'PASS' if report.passed else 'FAIL'} ({len(report.failures)} failing)")
    return 0 if report.passed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
