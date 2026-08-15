"""Units at every IR boundary — pint, one registry, no bare floats crossing.

§1 of robot-platform-tech-stack.md (v3) adds `pint` to the stack as "the Python
analogue of tscircuit's unit strings, same failure class". The failure class is
worth naming precisely, because it is not "someone forgot the unit": it is that
`20` reads as a torque to a human and as a float to an optimizer, so a servo
quoted in kgf*cm and a stepper quoted in N*m compare as 30 > 0.4 and the wrong
motor wins by a factor of ten. `catalogue._kgcm` exists because that already
happened here once.

Three rules this module enforces, and one it deliberately does not:

- **A unit string must parse.** `Quantity(unit="Nm/s")` is a typo that used to
  survive to a report; now it raises where it was written.
- **Conversion is arithmetic somebody else already got right.** `q.to("N*m")`
  rather than a `_KGCM_TO_NM` constant per module.
- **Dimensional comparison is checked, not assumed.** `expect(q, "torque")`
  refuses a current where a torque was wanted, which is the check that catches
  a catalogue field filled in from the wrong column of a datasheet table.

- *Not* enforced: torque and energy are dimensionally identical (N*m and J are
  both [M][L]^2/[T]^2), so `expect(energy_q, "torque")` passes. Dimensional
  analysis cannot separate them and this module does not pretend to. What it
  buys is the order-of-magnitude class of error, which is the one that has
  actually cost us a design.

The registry is process-wide and lazily built. pint quantities from two
different registries refuse to interoperate — a real and confusing failure —
so there is exactly one, and `registry()` is the only way to reach it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pint


class UnitError(ValueError):
    """A unit string that does not parse, or does not mean what was expected."""


@lru_cache(maxsize=1)
def registry() -> "pint.UnitRegistry":
    """The one process-wide pint registry.

    Lazily constructed: building a registry parses pint's full unit definition
    file and costs ~100 ms, which is real money in a coverage sweep that imports
    the engine and never converts anything.
    """
    try:
        import pint
    except ModuleNotFoundError as exc:  # pragma: no cover - environment, not logic
        # Distinguished from a bad unit string, because the two look identical
        # at the call site and need opposite fixes. The first version of this
        # reported a missing pint as "'N*m' is not a unit pint recognises",
        # which sent a reader looking for a typo in a unit that was correct.
        raise ModuleNotFoundError(
            "pint is not installed, so no Quantity can be constructed. It is a "
            "required dependency of `engine` (§1: units at every IR boundary) — "
            "`pip install pint`, or install the engine package rather than "
            "putting its src/ on PYTHONPATH."
        ) from exc

    return pint.UnitRegistry()


@lru_cache(maxsize=512)
def parse(unit: str) -> "pint.Unit":
    """Parse a unit string, cached.

    Cached hard, because `Quantity` validates on construction and a tier-1 sweep
    constructs tens of thousands of them from a handful of distinct unit strings.
    Uncached this turned a 40 ms evaluation into a 2 s one.
    """
    try:
        return registry().Unit(unit)
    except Exception as exc:  # pint raises several distinct types
        raise UnitError(
            f"{unit!r} is not a unit pint recognises ({type(exc).__name__}: {exc}). "
            "Use a plain unit expression: 'N*m', 'kg/m^3', 'rad/s', 'A', 'V', 'Ah'."
        ) from None


def is_unit(unit: str) -> bool:
    try:
        parse(unit)
    except UnitError:
        return False
    return True


@lru_cache(maxsize=4096)
def _factor(from_unit: str, to_unit: str) -> float:
    src, dst = parse(from_unit), parse(to_unit)
    if src.dimensionality != dst.dimensionality:
        raise UnitError(
            f"cannot convert {from_unit!r} ({src.dimensionality}) to {to_unit!r} "
            f"({dst.dimensionality}): different physical dimensions"
        )
    ureg = registry()
    return float(ureg.Quantity(1.0, src).to(dst).magnitude)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Scale `value` from one unit to another. Refuses across dimensions.

    Only correct for multiplicative units, which is every unit this platform
    uses. Absolute temperature (degC -> K) has an offset and is deliberately not
    supported: nothing in the IR carries an absolute temperature, and a silently
    wrong 273.15 is worse than a refusal.
    """
    if from_unit == to_unit:
        return value
    src = parse(from_unit)
    if getattr(src, "_units", None) is not None and any(
        name in ("degree_Celsius", "degree_Fahrenheit") for name in src._units
    ):
        raise UnitError(
            f"{from_unit!r} is an offset unit; convert() handles multiplicative units only"
        )
    return value * _factor(from_unit, to_unit)


# Semantic name -> a unit that has the dimensionality that name means. Named
# rather than written as dimensionality strings because `[mass] * [length] ** 2
# / [time] ** 3 / [current]` at a call site tells a reader nothing, and "voltage"
# tells them everything.
_DIMENSION_EXEMPLARS: dict[str, str] = {
    "length": "m",
    "mass": "kg",
    "time": "s",
    "angle": "rad",
    "angular_velocity": "rad/s",
    "torque": "N*m",
    "force": "N",
    "current": "A",
    "voltage": "V",
    "resistance": "ohm",
    "power": "W",
    "energy": "J",
    "charge": "Ah",
    "capacity": "Ah",
    "density": "kg/m^3",
    "pressure": "Pa",
    "area": "m^2",
    "volume": "m^3",
    "dimensionless": "",
}


def dimension_of(unit: str) -> str:
    """A stable string for a unit's dimensionality, for comparisons/messages."""
    return str(parse(unit).dimensionality)


def compatible(unit: str, semantic: str) -> bool:
    """Does `unit` have the dimensionality `semantic` names?"""
    exemplar = _DIMENSION_EXEMPLARS.get(semantic)
    if exemplar is None:
        raise UnitError(
            f"unknown semantic dimension {semantic!r}; known: {sorted(_DIMENSION_EXEMPLARS)}"
        )
    return parse(unit).dimensionality == parse(exemplar).dimensionality


def expect(quantity, semantic: str, *, what: str = "value"):
    """Assert a Quantity's unit means what the caller needs, and return it.

    The catch is a datasheet transcribed from the wrong column: a `rated_current`
    field filled in with the voltage rating parses fine, passes every existing
    validator, and then quietly sizes the rail. Here it raises at the boundary.
    """
    if not compatible(quantity.unit, semantic):
        raise UnitError(
            f"{what} is {quantity.unit!r} ({dimension_of(quantity.unit)}) but "
            f"{semantic} was required ({dimension_of(_DIMENSION_EXEMPLARS[semantic] or 'm/m')})"
        )
    return quantity
