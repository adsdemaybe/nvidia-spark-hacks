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
        """True only if something was actually measured and all of it passed.

        `all([])` is True, so without the emptiness guard a design that ran zero
        criteria — no criteria registered, every tier skipped, a robot no criterion
        applies to — reports PASS and the CLI exits 0. That is the precise
        inversion of "agents propose, the harness disposes": silence would become
        consent. No evidence is not a pass.
        """
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CriterionResult]:
        return [r for r in self.results if not r.passed]


def compute_mass_properties(ir: RobotIR) -> dict[str, MassProperties]:
    return {link.id: build_geometry(link.geometry).mass_properties for link in ir.links}


# The tier ladder is fixed by §3 — analytic, Pinocchio, MuJoCo, Drake — not
# discovered from whatever happens to be registered. That distinction is what
# lets a report say "tier 2 did not run" instead of not mentioning tier 2.
TIERS = (0, 1, 2, 3)


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

    # A tier with no criteria registered at all never enters the loop above, so
    # it could never be reported skipped: asking for `max_tier=3` ran tiers 0-1,
    # said "skipped: []", and returned PASS — presenting a design that has seen
    # no contact simulation and no equilibrium proof as though it had passed
    # both. §3 and §11 non-negotiable #5 say the opposite in as many words: "a
    # tier that didn't run is reported skipped, never silently treated as a
    # pass". The tiers are fixed by §3, so absence is knowable here.
    tiers_skipped |= {tier for tier in TIERS if tier <= max_tier and tier not in tiers_run}

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
    if not report.results:
        # Otherwise this prints "FAIL (0 failing)", which reads like a bug report
        # about the reporter rather than the actual situation.
        print("overall: FAIL — no criteria ran, so nothing was verified")
    else:
        print(f"overall: {'PASS' if report.passed else 'FAIL'} ({len(report.failures)} failing)")
    return 0 if report.passed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
