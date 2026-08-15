"""r2s.core -- contracts, provenance, artifact store, gate runner.

Deliberately free of heavy dependencies. Nothing here imports torch, CUDA, a
simulator, or an LLM client; that absence is what makes the gate boundary a
structural fact rather than a policy.
"""
from .artifacts import (
    ArtifactHeader,
    ArtifactRef,
    BaseArtifact,
    CodeVersion,
    Degradation,
    DegradationLevel,
    canonical_json,
    inherit_degradation,
    sha256_of,
)
from .gates import ExitCode, GateReport, GateResult, Severity, Verdict, gate, run_gates
from .geometry import AABB, OBB, Plane, Pose, SupportSurface
from .provenance import (
    AssetProvenance,
    DensityPrior,
    Measured,
    Proposer,
    ValueProvenance,
    assumed,
    inferred,
    measured,
)

__all__ = [
    "AABB",
    "OBB",
    "ArtifactHeader",
    "ArtifactRef",
    "AssetProvenance",
    "BaseArtifact",
    "CodeVersion",
    "Degradation",
    "DegradationLevel",
    "DensityPrior",
    "ExitCode",
    "GateReport",
    "GateResult",
    "Measured",
    "Plane",
    "Pose",
    "Proposer",
    "Severity",
    "SupportSurface",
    "ValueProvenance",
    "Verdict",
    "assumed",
    "canonical_json",
    "gate",
    "inferred",
    "inherit_degradation",
    "measured",
    "run_gates",
    "sha256_of",
]
