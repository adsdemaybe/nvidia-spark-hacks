"""Contract tests for Measured[T] / ValueProvenance (r2s.core.provenance)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from r2s.core.provenance import (
    Measured,
    ValueProvenance,
    assumed,
    inferred,
    measured,
)


class TestEvidenceRequirements:
    def test_confirmed_without_source_rejected(self):
        with pytest.raises(ValidationError, match="requires a `source`"):
            Measured[float](value=1.0, unit="kg", provenance=ValueProvenance.CONFIRMED)

    def test_measured_without_source_rejected(self):
        with pytest.raises(ValidationError, match="requires a `source`"):
            Measured[float](value=1.0, unit="kg", provenance=ValueProvenance.MEASURED)

    def test_empty_source_counts_as_missing(self):
        with pytest.raises(ValidationError, match="requires a `source`"):
            Measured[float](
                value=1.0, unit="kg", provenance=ValueProvenance.CONFIRMED, source=""
            )

    def test_inferred_without_method_rejected(self):
        with pytest.raises(ValidationError, match="requires a `method`"):
            Measured[float](value=1.0, unit="kg", provenance=ValueProvenance.INFERRED)

    def test_confirmed_with_source_accepted(self):
        m = Measured[float](
            value=2700.0,
            unit="kg/m^3",
            provenance=ValueProvenance.CONFIRMED,
            source="https://example.com/aluminum-datasheet",
        )
        assert m.provenance is ValueProvenance.CONFIRMED
        assert m.is_evidenced

    def test_measured_with_source_accepted(self):
        m = Measured[float](
            value=0.312,
            unit="kg",
            provenance=ValueProvenance.MEASURED,
            source="capture/run_001/scale_photo.jpg",
        )
        assert m.provenance is ValueProvenance.MEASURED
        assert m.is_evidenced

    def test_inferred_with_method_accepted(self):
        m = Measured[float](
            value=0.15,
            unit="kg",
            provenance=ValueProvenance.INFERRED,
            method="uniform_density_from_category_prior",
        )
        assert m.provenance is ValueProvenance.INFERRED
        assert m.is_evidenced

    def test_assumed_needs_no_evidence(self):
        m = Measured[float](value=0.5, unit="1")
        assert m.provenance is ValueProvenance.ASSUMED
        assert not m.is_evidenced


class TestHelpers:
    def test_assumed_helper(self):
        m = assumed(0.5, "1", note="household default")
        assert m.provenance is ValueProvenance.ASSUMED
        assert m.value == 0.5
        assert m.unit == "1"
        assert not m.is_evidenced

    def test_assumed_helper_without_note(self):
        m = assumed(9.81, "m/s^2")
        assert m.provenance is ValueProvenance.ASSUMED
        assert m.method is None

    def test_inferred_helper(self):
        m = inferred(0.15, "kg", method="uniform_density_prior[box]")
        assert m.provenance is ValueProvenance.INFERRED
        assert m.method == "uniform_density_prior[box]"
        assert m.is_evidenced

    def test_inferred_helper_kwargs_pass_through(self):
        m = inferred(0.15, "kg", method="m", stddev=0.02, bounds=(0.1, 0.2))
        assert m.stddev == 0.02
        assert m.bounds == (0.1, 0.2)

    def test_measured_helper(self):
        m = measured(0.35, "m", source="tape_measure_photo_003")
        assert m.provenance is ValueProvenance.MEASURED
        assert m.source == "tape_measure_photo_003"
        assert m.is_evidenced

    def test_helpers_work_for_tuple_values(self):
        m = inferred((0.0, 0.0, 0.05), "m", method="centroid_of_watertight_mesh")
        assert m.value == (0.0, 0.0, 0.05)


class TestImmutability:
    def test_value_frozen(self):
        m = assumed(1.0, "kg")
        with pytest.raises(ValidationError):
            m.value = 2.0

    def test_provenance_cannot_be_upgraded_in_place(self):
        m = assumed(1.0, "kg")
        with pytest.raises(ValidationError):
            m.provenance = ValueProvenance.CONFIRMED

    def test_unit_frozen(self):
        m = assumed(1.0, "kg")
        with pytest.raises(ValidationError):
            m.unit = "g"
