"""§1 (v3): pint at every IR boundary. A bare float never crosses an interface."""

from __future__ import annotations

import pytest

from engine.ir import Provenance, Quantity
from engine.units import UnitError, compatible, convert, expect, parse


def _p() -> Provenance:
    return Provenance(status="ASSUMED", source="", note="test")


def test_every_unit_the_catalogue_already_uses_parses():
    # The migration risk: adding validation to `Quantity` breaks the catalogue
    # if any existing unit string is not a unit. This is that check, and it is a
    # test rather than a comment because the catalogue keeps growing.
    for unit in ["A", "arcmin", "deg", "kg", "kg/m^3", "m", "N*m", "Pa", "rad/s",
                 "V", "mm", "rad", "W", "A*h", "Hz", "byte", "count", "ohm", "m^2", "s"]:
        assert parse(unit) is not None


def test_a_typo_in_a_unit_fails_where_it_is_written():
    with pytest.raises(ValueError) as exc:
        Quantity(value=1.0, unit="Nm/ss", provenance=_p())
    assert "not a unit pint recognises" in str(exc.value)


def test_conversion_replaces_the_hand_written_factor():
    # 30 kgf*cm is the Feetech STS3215's headline number, and treating it as
    # N*m is the factor-of-ten error `catalogue._kgcm` exists to prevent.
    q = Quantity(value=30.0, unit="kgf*cm", provenance=_p())
    assert q.magnitude_in("N*m") == pytest.approx(2.942, rel=1e-3)


def test_conversion_carries_provenance_rather_than_downgrading_it():
    q = Quantity(
        value=30.0,
        unit="kgf*cm",
        provenance=Provenance(status="CONFIRMED", source="https://example/ds.pdf"),
    )
    # Expressing a confirmed number in different units does not make it less
    # confirmed — it makes it more usable.
    assert q.to("N*m").provenance.status == "CONFIRMED"


def test_conversion_across_dimensions_refuses():
    with pytest.raises(UnitError) as exc:
        convert(1.0, "A", "V")
    assert "different physical dimensions" in str(exc.value)


def test_expect_catches_a_value_read_from_the_wrong_datasheet_column():
    # The failure this exists for: `rated_current` filled in from the voltage
    # column. It parses, it has provenance, and it is wrong in a way only
    # dimensional analysis sees.
    voltage = Quantity(value=12.0, unit="V", provenance=_p())
    with pytest.raises(UnitError) as exc:
        expect(voltage, "current", what="rated_current")
    assert "current was required" in str(exc.value)


def test_torque_and_energy_are_not_separable_and_we_say_so():
    # Documented limitation, asserted so it cannot quietly change: N*m and J
    # share a dimensionality, so `expect` cannot tell a torque from an energy.
    assert compatible("J", "torque")


def test_offset_units_are_refused_rather_than_silently_wrong():
    with pytest.raises(UnitError) as exc:
        convert(25.0, "degC", "K")
    assert "offset unit" in str(exc.value)
