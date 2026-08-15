"""
Model-agnostic scoring harness.

The original harness was written for the rover and then copied for the
quadruped, and the rocket ended up driven by hand — three implementations of one
idea. This is the single implementation. A model module plugs in by exposing:

    DESIGN_VARS    {name: (lo, hi)}          continuous, searchable
    DISCRETE_VARS  {name: (choice, ...)}     catalogue choices           [optional]
    CHECKS         ((name, check_fn, metric_fn|None), ...)
    current_design()  -> dict
    reconfigure(**overrides) -> None

`check_fn` returns a list of human-readable failure strings (empty = pass).
`metric_fn` returns (value, target) so the criterion is MEASURABLE rather than
merely boolean — which matters because the critic's coverage test works on
magnitude, and a purely boolean criterion is invisible to it.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE,) + tuple(os.path.join(_HERE, d) for d in
                           ("roverkit", "quadkit", "rocketkit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODELS = {
    "rover": "rover_arm",
    "quad": "quadruped",
    "rocket": "rocket",
}


def load(name: str):
    if name in MODELS:
        name = MODELS[name]
    return importlib.import_module(name)


@dataclass
class Check:
    name: str
    ok: bool
    value: float = 0.0
    target: float = 0.0
    note: str = ""

    def violation(self) -> float:
        if self.ok:
            return 0.0
        if self.target:
            return abs(self.target - self.value) / abs(self.target)
        return 1.0


@dataclass
class Report:
    checks: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def score(self) -> float:
        return sum(c.violation() for c in self.checks)

    def failing(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


def full_design(model) -> dict:
    d = dict(model.current_design())
    return d


def evaluate(model, design: dict) -> Report:
    rep = Report()
    try:
        model.reconfigure(**design)
    except Exception as exc:                                    # noqa: BLE001
        rep.checks.append(Check("reconfigure", False, note=str(exc)[:110]))
        return rep

    for entry in model.CHECKS:
        name, check_fn = entry[0], entry[1]
        metric_fn = entry[2] if len(entry) > 2 else None
        try:
            fails = check_fn()
            value, target = (metric_fn() if metric_fn else (0.0, 0.0))
            rep.checks.append(Check(name, not fails, value=float(value),
                                    target=float(target),
                                    note=fails[0] if fails else
                                    (f"{value:.2f} vs {target:.2f}"
                                     if metric_fn else "ok")))
        except Exception as exc:                                # noqa: BLE001
            rep.checks.append(
                Check(name, False, note=f"{type(exc).__name__}: {exc}"[:110]))
    return rep


def report_dict(rep: Report) -> dict:
    return {
        "passed": bool(rep.passed),
        "score": round(float(rep.score), 4),
        "failing": list(rep.failing()),
        "checks": [
            {"name": str(c.name), "ok": bool(c.ok),
             "value": round(float(c.value), 4),
             "target": round(float(c.target), 4), "note": str(c.note)}
            for c in rep.checks
        ],
    }


def refine(model, start: dict, max_iters: int = 30, verbose: bool = True):
    """Coordinate descent over continuous vars plus catalogue search."""
    design = dict(start)
    rep = evaluate(model, design)
    hist = [(0, dict(design), rep)]
    if verbose:
        print(f"iter  0  score {rep.score:7.3f}  failing: {rep.failing() or 'none'}")

    step = {k: (hi - lo) * 0.25 for k, (lo, hi) in model.DESIGN_VARS.items()}
    discrete = getattr(model, "DISCRETE_VARS", {})
    it = 0
    while it < max_iters:
        it += 1
        best, best_rep, best_design = rep.score, rep, None

        for var, opts in discrete.items():
            for choice in opts:
                if design.get(var) == choice:
                    continue
                cand = dict(design); cand[var] = choice
                cr = evaluate(model, cand)
                if cr.score < best - 1e-9:
                    best, best_rep, best_design = cr.score, cr, cand

        for var, (lo, hi) in model.DESIGN_VARS.items():
            for d in (+1, -1):
                cand = dict(design)
                cand[var] = min(hi, max(lo, float(cand[var]) + d * step[var]))
                if abs(cand[var] - float(design[var])) < 1e-9:
                    continue
                cr = evaluate(model, cand)
                if cr.score < best - 1e-9:
                    best, best_rep, best_design = cr.score, cr, cand

        if best_design is None:
            step = {k: v * 0.5 for k, v in step.items()}
            if max(step.values()) < 1e-4:
                break
            continue

        design, rep = best_design, best_rep
        hist.append((it, dict(design), rep))
        if verbose:
            print(f"iter {it:2d}  score {rep.score:7.3f}  "
                  f"failing: {rep.failing() or 'none'}")
        if rep.passed:
            break
    return design, rep, hist


def critique(model, design: dict, radius: float = 0.10,
             coverage_min: float = 0.02, only=None) -> dict:
    """Perturb every design variable and measure which criteria respond."""
    ref = report_dict(evaluate(model, design))
    ref_val = {c["name"]: c["value"] for c in ref["checks"]}
    ref_ok = {c["name"]: c["ok"] for c in ref["checks"]}

    findings, probes = [], []
    for var in model.DESIGN_VARS:
        if only and var not in only:
            continue
        lo, hi = model.DESIGN_VARS[var]
        step = (hi - lo) * radius
        rel, broke = {}, []
        for d in (+1, -1):
            cand = dict(design)
            cand[var] = min(hi, max(lo, float(design[var]) + d * step))
            if abs(cand[var] - float(design[var])) < 1e-9:
                continue
            cr = report_dict(evaluate(model, cand))
            for c in cr["checks"]:
                delta = c["value"] - ref_val.get(c["name"], 0.0)
                if abs(delta) > 1e-6:
                    denom = abs(ref_val.get(c["name"], 0.0)) or 1.0
                    rel[c["name"]] = max(abs(delta) / denom, rel.get(c["name"], 0.0))
                if bool(c["ok"]) != bool(ref_ok.get(c["name"], c["ok"])):
                    rel[c["name"]] = 1.0
                if ref_ok.get(c["name"]) and not c["ok"]:
                    broke.append((c["name"], cand[var], c["note"]))
        covered = sorted(k for k, v in rel.items() if v >= coverage_min)
        probes.append({"variable": var, "step": round(step, 4),
                       "relative": {k: round(v, 5) for k, v in rel.items()},
                       "covered_by": covered})
        for nm, val, note in broke:
            findings.append({"kind": "FRAGILE", "variable": var,
                             "detail": f"{var} -> {val:g} flips {nm} to FAIL ({note})"})
        if not covered:
            weak = ", ".join(f"{k} {v*100:.1f}%" for k, v in sorted(rel.items())) or "nothing"
            findings.append({"kind": "BLIND", "variable": var,
                             "detail": f"{var}: no criterion responds by more than "
                                       f"{coverage_min:.0%} ({weak})"})

    fragile = [f for f in findings if f["kind"] == "FRAGILE"]
    blind = [f for f in findings if f["kind"] == "BLIND"]
    return {"verdict": "reject" if fragile else ("revise" if blind else "accept"),
            "coverage_threshold": coverage_min,
            "baseline_passed": ref["passed"], "baseline_score": ref["score"],
            "findings": findings, "probes": probes}
