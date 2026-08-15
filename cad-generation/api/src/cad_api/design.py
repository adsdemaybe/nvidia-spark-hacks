"""Prompt -> robot. The generic text-to-CAD entry point.

    python -m cad_api.design "a four-wheeled rover about 30cm long"
    python -m cad_api.design "a 3-link robot arm on a fixed base"
    python -m cad_api.design "a two-wheeled balancing robot"

Nothing here knows what a rover is. The intent is whatever the caller types; the
topology comes from whatever `RobotIR` the model proposes; the verdict comes from
`engine.evaluate()`. Adding a new robot *type* requires no code change here — at
most a new geometry generator in the registry, which is §2's whole point
("a registry, never a hierarchy").

The only robot-shaped thing in this module is the empty string: there is no
default intent, because a default intent is a hardcoded robot.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.agent_loop import LoopStep, run_design_loop


@dataclass(frozen=True)
class DesignRobotResult:
    intent: str
    converged: bool
    steps: list[LoopStep]
    elapsed_s: float
    ir: Any | None  # engine.ir.RobotIR of the final revision
    evaluation: dict | None
    artifacts: dict[str, str]
    assembly: dict | None
    prediction_accuracy: dict[str, bool]
    coverage: list[dict] | None


def design_robot(
    intent: str,
    *,
    max_tier: int = 1,
    max_iterations: int = 4,
    provider: str = "qwen",
    model: str | None = None,
    base_url: str | None = None,
    artifact_dir: Path | None = None,
    stem: str | None = None,
    with_coverage: bool = True,
    on_reject: Any | None = None,
) -> DesignRobotResult:
    """Run the design loop for `intent`, then build whatever it produced.

    Geometry is exported for the final revision **whether or not it passed**.
    A failing design that can still be built is far more useful to look at than
    a bare verdict — and hiding it would make a failure indistinguishable from
    a crash.
    """
    if not intent or not intent.strip():
        raise ValueError("intent is required — there is no default robot")

    t0 = time.time()
    steps = run_design_loop(
        intent,
        max_tier=max_tier,
        max_iterations=max_iterations,
        provider=provider,
        model=model,
        base_url=base_url,
        on_reject=on_reject,
    )
    elapsed = time.time() - t0

    if not steps:
        return DesignRobotResult(
            intent=intent, converged=False, steps=[], elapsed_s=elapsed,
            ir=None, evaluation=None, artifacts={}, assembly=None, prediction_accuracy={},
            coverage=None,
        )

    final = steps[-1]

    # The agent stated pass/fail expectations before the harness ran. Scoring
    # them is how the platform keeps model-vs-harness disagreement visible (§7).
    accuracy: dict[str, bool] = {}
    for step in steps:
        accuracy.update(step.prediction_correct)

    evaluation = {
        "passed": final.report.passed,
        "tiers_run": final.report.tiers_run,
        "tiers_skipped": final.report.tiers_skipped,
        "results": [
            {"name": r.name, "passed": r.passed, "magnitude": r.magnitude,
             "unit": r.unit, "detail": r.detail}
            for r in final.report.results
        ],
    }

    # §8: coverage is what tells you what the harness *isn't* measuring. A design
    # that passes every criterion while a variable reads BLIND has not been
    # verified in that variable at all — the pass is silence, not evidence — so
    # this runs on the final design whether it converged or not, and reports
    # alongside the verdict rather than behind a separate command nobody runs.
    coverage: list[dict] | None = None
    if with_coverage:
        from engine.coverage import analyze_coverage

        try:
            coverage = [
                {
                    "link_id": c.link_id,
                    "param_name": c.param_name,
                    "baseline_value": c.baseline_value,
                    "blind": c.blind,
                    "fragile_criteria": c.fragile_criteria,
                    "max_response": max(c.responses.values(), default=0.0),
                    "responses": c.responses,
                }
                for c in analyze_coverage(final.revision.ir, max_tier=max_tier)
            ]
        except Exception as e:  # a coverage failure must not lose the design
            coverage = [{"error": f"{type(e).__name__}: {e}"}]

    artifacts: dict[str, str] = {}
    assembly_info: dict | None = None
    if artifact_dir is not None:
        from cad_api.assembly import assemble, export

        try:
            asm = assemble(final.revision.ir)
            bb = asm.part.bounding_box()
            assembly_info = {
                "links": len(asm.links),
                "joints": len(final.revision.ir.joints),
                "bbox_mm": [bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z],
                "total_mass_kg": asm.total_mass_kg,
                "per_link_mass_kg": {pl.link_id: pl.mass_kg for pl in asm.links},
            }
            artifacts = export(asm, Path(artifact_dir), stem or _slug(final.revision.ir.name))
        except Exception as e:  # geometry can fail even when the IR validates
            assembly_info = {"error": f"{type(e).__name__}: {e}"}

    return DesignRobotResult(
        intent=intent,
        converged=final.report.passed,
        steps=steps,
        elapsed_s=elapsed,
        ir=final.revision.ir,
        evaluation=evaluation,
        artifacts=artifacts,
        assembly=assembly_info,
        prediction_accuracy=accuracy,
        coverage=coverage,
    )


def _slug(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in name.strip().lower()]
    return "".join(keep).strip("-") or "robot"


def preflight(base_url: str, model: str) -> tuple[bool, str]:
    """Verify the server is up, serving `model`, and can do constrained decoding.

    Liveness is not enough: a server that ignores `response_format` answers
    /v1/models perfectly and then lets the model free-run past the context limit
    on every proposal, so checking only that it responds would green-light a run
    that cannot succeed. Probe the one capability the design loop depends on.
    """
    import urllib.error
    import urllib.request

    root = base_url.rstrip("/")
    try:
        with urllib.request.urlopen(root + "/models", timeout=10) as r:
            served = {m["id"] for m in json.load(r).get("data", [])}
    except Exception as e:
        return False, f"{root} unreachable ({type(e).__name__})"
    if model not in served:
        return False, f"server serves {sorted(served)}, not {model!r}"

    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "additionalProperties": False,
    }
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the JSON object {\"n\": 1}."}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "ping", "schema": schema}},
        "max_tokens": 64,
    }).encode()
    req = urllib.request.Request(root + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        return False, f"structured-output probe rejected: {e.read().decode()[:200]}"
    except Exception as e:
        return False, f"structured-output probe failed ({type(e).__name__})"
    content = ((d.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    content = content.strip().removeprefix("</think>").strip()
    try:
        # Grammar-constrained output parses as the schema or the server ignored it.
        if not isinstance(json.loads(content).get("n"), int):
            raise ValueError("n missing")
    except Exception:
        return False, f"server ignored response_format (replied {content[:120]!r})"
    return True, f"{model} serving, constrained decoding works"


def describe(result: DesignRobotResult) -> str:
    lines: list[str] = []
    lines.append(f'intent: "{result.intent}"')
    lines.append(
        f"verdict: {'PASS' if result.converged else 'FAIL'} after "
        f"{len(result.steps)} revision(s), {result.elapsed_s:.0f}s"
    )
    if result.ir is not None:
        lines.append(f"design: {result.ir.name} — {len(result.ir.links)} links, {len(result.ir.joints)} joints")
    if result.evaluation:
        lines.append(f"tiers run: {result.evaluation['tiers_run']}  skipped: {result.evaluation['tiers_skipped']}")
        for r in result.evaluation["results"]:
            lines.append(
                f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['name']:<34} "
                f"magnitude={r['magnitude']:+.4f} {r['unit']}"
            )
    if result.prediction_accuracy:
        right = sum(1 for v in result.prediction_accuracy.values() if v)
        lines.append(f"agent prediction accuracy: {right}/{len(result.prediction_accuracy)}")
        for name, ok in sorted(result.prediction_accuracy.items()):
            if not ok:
                lines.append(f"  MISPREDICTED: {name}")
    if result.coverage:
        errored = [c for c in result.coverage if "error" in c]
        if errored:
            lines.append(f"coverage: FAILED — {errored[0]['error']}")
        else:
            blind = [c for c in result.coverage if c["blind"]]
            fragile = [c for c in result.coverage if c["fragile_criteria"]]
            lines.append(
                f"coverage: {len(result.coverage)} variables, "
                f"{len(blind)} BLIND, {len(fragile)} FRAGILE"
            )
            for c in blind:
                # §8: a BLIND variable is a bug in the harness, not the design —
                # no amount of search fixes it, so it needs saying out loud.
                lines.append(f"  BLIND    {c['link_id']}.{c['param_name']} — no criterion responds")
            for c in fragile:
                lines.append(
                    f"  FRAGILE  {c['link_id']}.{c['param_name']} — "
                    f"flips {', '.join(c['fragile_criteria'])} under ±10%"
                )
    if result.assembly:
        a = result.assembly
        if "error" in a:
            lines.append(f"assembly FAILED: {a['error']}")
        else:
            lines.append(
                f"geometry: {a['bbox_mm'][0]:.1f} x {a['bbox_mm'][1]:.1f} x {a['bbox_mm'][2]:.1f} mm, "
                f"{a['total_mass_kg'] * 1000:.1f} g"
            )
    for k, v in result.artifacts.items():
        lines.append(f"  {k:<20} {v}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m cad_api.design",
        description="Design a robot from a text prompt and export its geometry.",
    )
    ap.add_argument("intent", help="what to build, in plain text")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--max-tier", type=int, default=1)
    ap.add_argument("--max-iterations", type=int, default=4)
    ap.add_argument("--provider", default="qwen", choices=["qwen", "local", "openai"])
    ap.add_argument("--model", default="qwen3-coder-next")
    ap.add_argument("--base-url", default="http://localhost:8100/v1")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--no-coverage", action="store_true",
                    help="skip the §8 coverage sweep (2 extra evaluations per design variable)")
    args = ap.parse_args(argv)

    # Every provider name is the same transport (see run_design_loop), and they
    # all depend on the same constrained-decoding capability. Naming only one of
    # them here once meant the default provider skipped the probe that exists to
    # stop an unusable run before it burns twenty minutes.
    if not args.no_preflight:
        ok, why = preflight(args.base_url, args.model)
        print(f"preflight: {'OK' if ok else 'FAILED'} — {why}")
        if not ok:
            return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f'designing: "{args.intent}"\n')

    try:
        result = design_robot(
            args.intent,
            max_tier=args.max_tier,
            max_iterations=args.max_iterations,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            artifact_dir=out,
            with_coverage=not args.no_coverage,
            on_reject=lambda i, why: print(f"  revision {i} rejected: {why}\n", flush=True),
        )
    except Exception as exc:
        name = type(exc).__name__
        if "Connection" in name or "Timeout" in name:
            print(f"RUN LOST: the model server at {args.base_url} became unreachable ({name}).")
            print("Nothing was saved — the design loop returns only on completion.")
            return 3
        raise

    if result.ir is None:
        print("No revision produced a valid design — every proposal was rejected before evaluation.")
        return 1

    (out / "robot.ir.json").write_text(result.ir.model_dump_json(indent=2), encoding="utf-8")
    (out / "robot.meta.json").write_text(
        json.dumps(
            {
                "intent": result.intent,
                "converged": result.converged,
                "revisions": len(result.steps),
                "elapsed_s": round(result.elapsed_s, 1),
                "evaluation": result.evaluation,
                "assembly": result.assembly,
                "prediction_accuracy": result.prediction_accuracy,
                "coverage": result.coverage,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(describe(result))
    print(f"\nIR: {out / 'robot.ir.json'}")
    return 0 if result.converged else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
