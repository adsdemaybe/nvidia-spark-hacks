"""RunContext + the stage wrapper every pipeline stage runs inside.

A stage is `fn(inputs, cfg, ctx) -> (payload, backend, degradations)`. The
wrapper owns everything a stage author must not be trusted to remember:

  * input digest + cache lookup (resume)
  * degradation inheritance (monotone)
  * gate execution and report attachment
  * writing the artifact even on FAILURE (marked, not promoted)
  * the run manifest

That the wrapper is not optional is the point.
"""
from __future__ import annotations

import json
import platform
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

import numpy as np
from pydantic import BaseModel

from .artifacts import (
    ArtifactHeader,
    ArtifactRef,
    BaseArtifact,
    CodeVersion,
    Degradation,
    DegradationLevel,
    inherit_degradation,
    sha256_of,
)
from .gates import ExitCode, GateReport, run_gates
from .store import CASStore, input_digest


def _pkg_versions(names: list[str]) -> dict[str, str]:
    out = {}
    for n in names:
        try:
            out[n] = version(n)
        except PackageNotFoundError:
            out[n] = "absent"
    return out


@dataclass
class RunContext:
    run_id: str
    store: CASStore
    seed: int = 1337
    work_dir: Path = Path(".r2s/work")
    force_stages: set[str] = field(default_factory=set)
    allow_degraded: bool = True  # V1 default: partial results ship, labeled
    extra: dict[str, Any] = field(default_factory=dict)

    def rng(self, *scope: str | int) -> np.random.Generator:
        """Seed tree: master seed + scope path -> independent, reproducible stream.
        Never touch global np.random."""
        ss = np.random.SeedSequence([self.seed, *(_stable_int(s) for s in scope)])
        return np.random.Generator(np.random.PCG64(ss))

    def scratch(self, stage: str) -> Path:
        p = self.work_dir / self.run_id / stage
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -- run manifest ------------------------------------------------------
    def _manifest_path(self) -> str:
        return f"{self.store._base}/runs/{self.run_id}/manifest.json"

    def manifest(self) -> dict:
        p = self._manifest_path()
        if self.store.fs.exists(p):
            with self.store.fs.open(p, "rb") as f:
                return json.load(f)
        return {"run_id": self.run_id, "seed": self.seed, "stages": {}}

    def record_stage(self, stage: str, entry: dict) -> None:
        m = self.manifest()
        m["stages"][stage] = entry
        p = self._manifest_path()
        self.store.fs.makedirs(p.rsplit("/", 1)[0], exist_ok=True)
        with self.store.fs.open(p, "wb") as f:
            f.write(json.dumps(m, indent=2, sort_keys=True).encode())

    def promoted_ref(self, stage: str) -> ArtifactRef | None:
        e = self.manifest()["stages"].get(stage)
        if e and e.get("promoted"):
            return ArtifactRef.model_validate(e["artifact_ref"])
        return None


def _stable_int(s: str | int) -> int:
    if isinstance(s, int):
        return s
    return int.from_bytes(sha256_of(s)[:8].encode(), "big") % (2**31)


@dataclass
class StageSpec:
    name: str
    version: str  # bump when the algorithm changes enough to invalidate caches
    artifact_type: str
    payload_model: type[BaseModel]
    relevant_packages: list[str] = field(default_factory=lambda: ["r2s-core"])


class StageOutcome(BaseModel):
    stage: str
    artifact_ref: ArtifactRef
    gate_report: GateReport
    exit_code: int
    cached: bool = False
    promoted: bool = False
    wallclock_s: float = 0.0


StageFn = Callable[..., tuple[BaseModel, str, list[Degradation]]]


def run_stage(
    spec: StageSpec,
    fn: StageFn,
    inputs: dict[str, BaseArtifact],
    cfg: BaseModel | dict,
    ctx: RunContext,
    gate_subject: Any | None = None,
) -> StageOutcome:
    """Execute one stage with caching, gating, and honest failure recording."""
    input_refs = [
        ArtifactRef(
            uri=f"cas://sha256/{a.header.artifact_id}",
            sha256=a.header.artifact_id,
            size_bytes=0,
            media_type="application/json",
        )
        for a in inputs.values()
    ]
    digest = input_digest(
        stage=spec.name,
        stage_version=spec.version,
        inputs=input_refs,
        config=cfg,
        packages=_pkg_versions(spec.relevant_packages),
        seed=ctx.seed,
    )

    if spec.name not in ctx.force_stages:
        hit = ctx.store.lookup(spec.name, digest)
        if hit is not None:
            art = ctx.store.get_artifact(hit, BaseArtifact[spec.payload_model])  # type: ignore[valid-type]
            report = run_gates(spec.name, art, ctx, subject_id=art.header.artifact_id)
            outcome = StageOutcome(
                stage=spec.name,
                artifact_ref=hit,
                gate_report=report,
                exit_code=int(report.exit_code),
                cached=True,
                promoted=not report.blocked,
            )
            ctx.record_stage(spec.name, _entry(outcome, digest))
            return outcome

    t0 = time.perf_counter()
    payload, backend, own_degs = fn(inputs, cfg, ctx)
    wall = time.perf_counter() - t0

    level, degs = inherit_degradation(list(inputs.values()), own_degs)
    header = ArtifactHeader(
        schema_version=getattr(payload, "schema_version", "1.0.0"),
        artifact_type=spec.artifact_type,
        artifact_id=sha256_of(payload),
        run_id=ctx.run_id,
        stage=spec.name,
        backend=backend,
        input_digest=digest,
        input_refs=input_refs,
        code_version=CodeVersion(packages=_pkg_versions(spec.relevant_packages)),
        seed=ctx.seed,
        degradation=level,
        degradations=degs,
        wallclock_s=wall,
    )
    art = BaseArtifact(header=header, payload=payload)

    report = run_gates(spec.name, art, ctx, subject_id=header.artifact_id)
    # soft gate failures become degradation records on the header
    if report.degradations():
        level2, degs2 = inherit_degradation([art], report.degradations())
        header = header.model_copy(update={"degradation": level2, "degradations": degs2})
        art = BaseArtifact(header=header, payload=payload)

    # Written to the store even on hard failure -- inspectable, just not promoted.
    ref = ctx.store.put_artifact(art)
    promoted = not report.blocked
    if promoted:
        ctx.store.record(spec.name, digest, ref)

    outcome = StageOutcome(
        stage=spec.name,
        artifact_ref=ref,
        gate_report=report,
        exit_code=int(report.exit_code),
        cached=False,
        promoted=promoted,
        wallclock_s=wall,
    )
    ctx.record_stage(spec.name, _entry(outcome, digest))
    return outcome


def _entry(o: StageOutcome, digest: str) -> dict:
    return {
        "artifact_ref": o.artifact_ref.model_dump(mode="json"),
        "input_digest": digest,
        "verdict": o.gate_report.verdict,
        "exit_code": o.exit_code,
        "cached": o.cached,
        "promoted": o.promoted,
        "wallclock_s": round(o.wallclock_s, 3),
        "host": platform.node(),
    }


def load_input(ctx: RunContext, stage: str, model: type[BaseModel]) -> BaseArtifact:
    """Fetch a promoted upstream artifact or die with MISSING_INPUT semantics."""
    ref = ctx.promoted_ref(stage)
    if ref is None:
        raise SystemExit(int(ExitCode.MISSING_INPUT))
    return ctx.store.get_artifact(ref, BaseArtifact[model])  # type: ignore[valid-type]
