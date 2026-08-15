"""Degradation monotonicity contracts (r2s.core.artifacts)."""
from __future__ import annotations

from pydantic import BaseModel

from r2s.core.artifacts import (
    ArtifactHeader,
    BaseArtifact,
    Degradation,
    DegradationLevel,
    inherit_degradation,
)


class _Payload(BaseModel):
    x: int = 0


def _deg(code: str, level: DegradationLevel, stage: str = "recon") -> Degradation:
    return Degradation(code=code, level=level, stage=stage, message=f"{code} happened")


def _artifact(
    level: DegradationLevel, degradations: list[Degradation] | None = None
) -> BaseArtifact[_Payload]:
    header = ArtifactHeader(
        schema_version="1.0.0",
        artifact_type="capture",
        artifact_id="a" * 64,
        run_id="run_t",
        stage="test_stage",
        backend="stub",
        input_digest="d" * 64,
        degradation=level,
        degradations=degradations or [],
    )
    return BaseArtifact[_Payload](header=header, payload=_Payload())


class TestInheritDegradation:
    def test_stub_input_dominates_full_input(self):
        stub = _artifact(DegradationLevel.STUB)
        full = _artifact(DegradationLevel.FULL)
        level, records = inherit_degradation([stub, full])
        assert level is DegradationLevel.STUB
        assert records == []

    def test_full_inputs_with_own_degraded_gives_degraded(self):
        full_a = _artifact(DegradationLevel.FULL)
        full_b = _artifact(DegradationLevel.FULL)
        own = [_deg("SEG.NO_MASKS", DegradationLevel.DEGRADED, stage="seg")]
        level, records = inherit_degradation([full_a, full_b], own)
        assert level is DegradationLevel.DEGRADED
        assert records == own

    def test_all_full_no_own_stays_full(self):
        level, records = inherit_degradation(
            [_artifact(DegradationLevel.FULL), _artifact(DegradationLevel.FULL)]
        )
        assert level is DegradationLevel.FULL
        assert records == []

    def test_upstream_records_carried_forward_verbatim(self):
        upstream_rec = _deg("RECON.SCALE_ASSUMED", DegradationLevel.DEGRADED)
        inp = _artifact(DegradationLevel.DEGRADED, [upstream_rec])
        own_rec = _deg("ASSET.HULL_FALLBACK", DegradationLevel.DEGRADED, stage="assetize")
        level, records = inherit_degradation([inp], [own_rec])
        assert level is DegradationLevel.DEGRADED
        # upstream first, own after -- both present, unmodified
        assert records == [upstream_rec, own_rec]

    def test_record_level_dominates_header_level(self):
        # header says FULL but a STUB record is attached: worst_degradation
        # sees the record, so the child must be STUB too.
        inp = _artifact(
            DegradationLevel.FULL, [_deg("RECON.STUB_BACKEND", DegradationLevel.STUB)]
        )
        level, records = inherit_degradation([inp])
        assert level is DegradationLevel.STUB
        assert len(records) == 1

    def test_no_inputs_no_own_is_full(self):
        level, records = inherit_degradation([])
        assert level is DegradationLevel.FULL
        assert records == []

    def test_own_stub_dominates_degraded_input(self):
        inp = _artifact(DegradationLevel.DEGRADED)
        own = [_deg("ASSET.PRIMITIVE_ONLY", DegradationLevel.STUB, stage="assetize")]
        level, _ = inherit_degradation([inp], own)
        assert level is DegradationLevel.STUB


class TestWorstDegradation:
    def test_header_only(self):
        art = _artifact(DegradationLevel.DEGRADED)
        assert art.worst_degradation() is DegradationLevel.DEGRADED

    def test_record_worse_than_header(self):
        art = _artifact(
            DegradationLevel.FULL, [_deg("X.STUB", DegradationLevel.STUB)]
        )
        assert art.worst_degradation() is DegradationLevel.STUB

    def test_full_clean_artifact(self):
        art = _artifact(DegradationLevel.FULL)
        assert art.worst_degradation() is DegradationLevel.FULL
        assert art.degradation is DegradationLevel.FULL

    def test_level_ordering_sanity(self):
        assert DegradationLevel.FULL < DegradationLevel.DEGRADED < DegradationLevel.STUB
