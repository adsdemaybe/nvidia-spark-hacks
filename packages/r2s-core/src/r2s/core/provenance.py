"""Provenance primitives.

The project invariant is that no physical constant may enter the pipeline as a
bare float. Every one is a `Measured[T]` carrying where the number came from.

This is enforced, not documented: `Measured` refuses to validate if you claim
CONFIRMED or MEASURED without a source, or INFERRED without a method. You
cannot write `mass=0.35` and have it serialize. In practice V1 will be almost
entirely INFERRED (inertia from uniform density) and ASSUMED (friction) -- that
is fine and honest. What is not fine is a CONFIRMED with nothing behind it.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValueProvenance(StrEnum):
    """Where a scalar physical quantity came from. Ordered worst -> best."""

    ASSUMED = "ASSUMED"      # a default. no evidence. the floor, and the default.
    INFERRED = "INFERRED"    # computed from measured/confirmed inputs by a named method
    CONFIRMED = "CONFIRMED"  # from an external authority with a citation
    MEASURED = "MEASURED"    # from sensor data in THIS capture


class AssetProvenance(StrEnum):
    """Where an asset's geometry came from."""

    SCANNED = "SCANNED"        # derived from this capture's gaussians
    GENERATED = "GENERATED"    # text/image -> 3D model output
    PROCEDURAL = "PROCEDURAL"  # synthesized primitive (the fallback rung)


class Proposer(BaseModel):
    """Who suggested a non-verdict field.

    Attached to anything an LLM is allowed to touch: segmentation prompts, asset
    descriptions, placement priors, task instructions. A gate never carries one,
    because a gate is never proposed.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["deterministic", "llm", "human", "default"]
    detail: str = ""  # "llm:claude-opus-5" | "rule:category_density_table"


T = TypeVar("T")


class Measured(BaseModel, Generic[T]):
    """A physical quantity that knows where it came from.

    `unit` is SI and mandatory -- "kg", "m", "kg*m^2", "1" for dimensionless.
    Unit-carrying values are what let a downstream gate assert a mass is in a
    plausible range without guessing whether someone stored grams.
    """

    model_config = ConfigDict(frozen=True)

    value: T
    unit: str
    provenance: ValueProvenance = ValueProvenance.ASSUMED
    source: str | None = None  # required for CONFIRMED / MEASURED
    method: str | None = None  # required for INFERRED
    stddev: float | None = None
    bounds: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _require_evidence(self) -> Measured[T]:
        p = self.provenance
        if p in (ValueProvenance.CONFIRMED, ValueProvenance.MEASURED) and not self.source:
            raise ValueError(
                f"provenance={p} requires a `source` (a citation, datasheet URL, or the "
                f"capture artifact it was measured from). Use INFERRED with a `method`, "
                f"or ASSUMED, if you do not have one."
            )
        if p is ValueProvenance.INFERRED and not self.method:
            raise ValueError(
                "provenance=INFERRED requires a `method` naming how it was computed, "
                "e.g. 'uniform_density_from_category_prior'."
            )
        return self

    @property
    def is_evidenced(self) -> bool:
        """True if this value rests on something other than a default."""
        return self.provenance is not ValueProvenance.ASSUMED


def assumed(value: T, unit: str, note: str = "") -> Measured[T]:
    """The honest default. Use this rather than reaching for a stronger claim."""
    return Measured[T](value=value, unit=unit, provenance=ValueProvenance.ASSUMED, method=note or None)


def inferred(value: T, unit: str, method: str, **kw) -> Measured[T]:
    return Measured[T](
        value=value, unit=unit, provenance=ValueProvenance.INFERRED, method=method, **kw
    )


def measured(value: T, unit: str, source: str, **kw) -> Measured[T]:
    return Measured[T](
        value=value, unit=unit, provenance=ValueProvenance.MEASURED, source=source, **kw
    )


DETERMINISTIC = Proposer(kind="deterministic", detail="")
DEFAULT_PROPOSER = Proposer(kind="default", detail="")


class DensityPrior(BaseModel):
    """Category -> density, used to infer mass from mesh volume.

    Every entry carries its own provenance so an asset's mass can be traced back
    to the table row that produced it. Hollow objects (mugs, boxes, bottles --
    i.e. most manipulation targets) are systematically overestimated by uniform
    density; `hollow_factor` is the correction and is itself ASSUMED until
    someone weighs something.
    """

    category: str
    density: Measured[float] = Field(description="kg/m^3")
    hollow_factor: Measured[float] = Field(description="dimensionless multiplier on mass")
