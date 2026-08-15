"""Gate registry: the "agents propose, the harness disposes" boundary.

A gate is a deterministic function that returns a verdict. That is its only
legal output. The boundary is enforced structurally rather than by convention:
`r2s-core` declares no LLM client dependency, so a module importing this one
physically cannot call a model. Proposal code (segmentation prompts, asset
descriptions, placement priors, task instructions) lives in `scan`/`envgen` and
must not import from here.

Gates always report `measured` alongside `thresholds` so a failure explains
itself without anyone reading the source.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import ArtifactRef


class Severity(StrEnum):
    HARD = "HARD"  # blocks promotion, always
    SOFT = "SOFT"  # emits a Degradation, pipeline continues, artifact marked DEGRADED
    INFO = "INFO"  # recorded, never blocks


class Verdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_id: str
    verdict: Verdict
    severity: Severity
    measured: dict[str, float | int | str | bool] = Field(default_factory=dict)
    thresholds: dict[str, float | int | str] = Field(default_factory=dict)
    message: str = ""
    evidence: list[ArtifactRef] = Field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def blocking(self) -> bool:
        return self.verdict is Verdict.FAIL and self.severity is Severity.HARD


class GateSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    id: str
    stage: str
    severity: Severity
    describe: str
    remediation: str
    fn: Callable[..., GateResult]
    subject: str = "artifact"  # "artifact" | "scene" | "asset" | "set"


_REGISTRY: dict[str, GateSpec] = {}


def gate(
    *,
    id: str,
    stage: str,
    severity: Severity,
    describe: str,
    remediation: str,
    subject: str = "artifact",
):
    """Register a deterministic check.

    The decorated function must return a `GateResult` and must not call an LLM,
    mutate its subject, or read global state. It receives (subject, ctx).
    """

    def deco(fn: Callable[..., GateResult]) -> Callable[..., GateResult]:
        if id in _REGISTRY:
            raise ValueError(f"duplicate gate id {id!r} (already registered by {_REGISTRY[id].fn})")
        _REGISTRY[id] = GateSpec(
            id=id,
            stage=stage,
            severity=severity,
            describe=describe,
            remediation=remediation,
            fn=fn,
            subject=subject,
        )
        fn.gate_id = id  # type: ignore[attr-defined]
        return fn

    return deco


def gates_for(stage: str) -> list[GateSpec]:
    return sorted((g for g in _REGISTRY.values() if g.stage == stage), key=lambda g: g.id)


def get_gate(gate_id: str) -> GateSpec | None:
    return _REGISTRY.get(gate_id)


def all_gates() -> list[GateSpec]:
    return sorted(_REGISTRY.values(), key=lambda g: (g.stage, g.id))


def ok(gate_id: str, spec: GateSpec, measured: dict, thresholds: dict, msg: str = "") -> GateResult:
    return GateResult(
        gate_id=gate_id,
        verdict=Verdict.PASS,
        severity=spec.severity,
        measured=measured,
        thresholds=thresholds,
        message=msg,
    )


def fail(gate_id: str, spec: GateSpec, measured: dict, thresholds: dict, msg: str) -> GateResult:
    """A failed SOFT gate is a WARN; a failed HARD gate is a FAIL."""
    return GateResult(
        gate_id=gate_id,
        verdict=Verdict.FAIL if spec.severity is Severity.HARD else Verdict.WARN,
        severity=spec.severity,
        measured=measured,
        thresholds=thresholds,
        message=msg,
    )


def check(
    gate_id: str,
    passed: bool,
    *,
    measured: dict[str, Any],
    thresholds: dict[str, Any],
    msg_pass: str = "",
    msg_fail: str = "",
) -> GateResult:
    """Convenience for the common one-condition gate."""
    spec = _REGISTRY[gate_id]
    return (
        ok(gate_id, spec, measured, thresholds, msg_pass)
        if passed
        else fail(gate_id, spec, measured, thresholds, msg_fail or spec.describe)
    )
