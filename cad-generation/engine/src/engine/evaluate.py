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
from engine.crosscheck import PipelineBug
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
    # Subsystems whose criteria are registered but produced nothing, because the
    # design does not describe them yet. §12 non-negotiable #5 is about tiers;
    # this is the same rule one level down. A robot with no `electronics` runs
    # every electronics criterion and gets zero results from each — without this
    # field the report is indistinguishable from one where they all passed.
    unmodelled: list[str] = field(default_factory=list)
    # Disagreements between two independent implementations of the same number
    # (§3). Deliberately not in `results`: a pipeline bug has no magnitude a
    # design change can move, and handing one to a design agent sends it off to
    # optimise a quantity that was never about the design.
    pipeline_bugs: list["PipelineBug"] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """PASS, FAIL, or BLOCKED.

        BLOCKED is the state `passed` cannot express: every criterion passed,
        and the harness that said so is known to be internally inconsistent. A
        PASS from a harness whose two mass models disagree by 20% is not a
        weaker pass, it is not a pass at all.
        """
        if self.pipeline_bugs:
            return "BLOCKED"
        return "PASS" if self.passed else "FAIL"

    @property
    def worst_provenance(self) -> str:
        """The weakest status among every criterion that ran (§12 #3).

        This is the line that stops a report from overselling itself: a tip-over
        PASS built on ASSUMED friction is an ASSUMED pass, and saying so is the
        difference between a verdict and a claim.
        """
        from engine.ir import worst_provenance as _worst

        if not self.results:
            return "ASSUMED"
        return _worst(*(r.provenance for r in self.results))

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


def _electronics_criteria():
    """The criteria that go silent on a robot with no electronics subsystem.

    Resolved from the registry by module rather than by a hand-kept name list,
    so a criterion added to `tier0_electronics` is reported as unmodelled
    without anyone remembering to update this — the failure mode being a new
    electronics check that silently contributes nothing and is never missed.
    """
    return tuple(
        c
        for c in all_criteria()
        if getattr(c.fn, "__module__", "") == "engine.criteria.tier0_electronics"
    )


ELECTRONICS_CRITERIA = _electronics_criteria()


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

    unmodelled: list[str] = []
    if ir.electronics is None and any(c.tier <= max_tier for c in ELECTRONICS_CRITERIA):
        unmodelled.append(
            "electronics: the design declares no rails, boards or harnesses, so "
            f"{', '.join(sorted(c.name for c in ELECTRONICS_CRITERIA))} produced no results"
        )

    # Only once the simulator has actually been asked to build the model. Below
    # tier 2 there is no second implementation to disagree with, and filing "the
    # simulator did not run" as a pipeline bug would block every tier-0 sweep.
    pipeline_bugs: list[PipelineBug] = []
    if max_tier >= 2:
        from engine.crosscheck import against_simulation

        pipeline_bugs = against_simulation(ir, mass_properties)

    return EvaluationReport(
        design_id=str(ir.id),
        design_name=ir.name,
        results=results,
        tiers_run=sorted(tiers_run),
        tiers_skipped=sorted(tiers_skipped),
        unmodelled=unmodelled,
        pipeline_bugs=pipeline_bugs,
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
    for note in report.unmodelled:
        print(f"  [UNMODELLED] {note}")
    for bug in report.pipeline_bugs:
        print(f"  [PIPELINE BUG] {bug}")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(
            f"  [{status}] {r.name:30s} magnitude={r.magnitude:+.4f} {r.unit:9s} "
            f"{r.provenance:9s} {r.detail}"
        )
    if not report.results:
        # Otherwise this prints "FAIL (0 failing)", which reads like a bug report
        # about the reporter rather than the actual situation.
        print("overall: FAIL — no criteria ran, so nothing was verified")
    else:
        # §12 non-negotiable #3: the verdict states the worst provenance among
        # its inputs. A PASS on ASSUMED numbers is a different claim from a PASS
        # on MEASURED ones, and the one-word difference is the whole point.
        print(
            f"overall: {report.verdict} ({len(report.failures)} failing), "
            f"worst provenance among inputs: {report.worst_provenance}"
        )
    # 2 rather than 1 for BLOCKED: a caller scripting this needs to tell "the
    # design is wrong" from "the harness is wrong", and they are not the same
    # thing to do next.
    if report.pipeline_bugs:
        return 2
    return 0 if report.passed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
