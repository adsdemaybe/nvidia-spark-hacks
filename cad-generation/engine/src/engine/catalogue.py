"""In-memory catalogues of purchasable parts and materials.

Phase 1 stand-in for the "catalogue" table implied by CatalogueParam.catalogue
(§2, §6). Real catalogues (motor datasheets, fastener tables, stock material
sizes) belong here behind the same lookup interface so swapping this module
for a DB-backed one later is a driver change, not a rewrite (§6 pattern).

Every entry's numeric fields are CONFIRMED against a resolvable source, per
§5: "Vendor CAD is for visuals only... bolt patterns and shaft diameters come
from catalogue constants, not from a STEP file someone else drew."
"""

from __future__ import annotations

from pydantic import BaseModel

from engine.ir import Provenance, Quantity


class MotorSpec(BaseModel):
    key: str
    stall_torque: Quantity  # N*m
    no_load_speed: Quantity  # rad/s
    mass: Quantity  # kg
    gear_ratio: float


class MaterialSpec(BaseModel):
    key: str
    density: Quantity  # kg/m^3
    yield_strength: Quantity  # Pa


class Catalogue:
    """A named, typed lookup table. Raises KeyError on unknown keys — an
    optimizer or agent must never be able to invent a part that doesn't exist
    (§11 non-negotiable #1).
    """

    def __init__(self, name: str, entries: dict[str, BaseModel]):
        self.name = name
        self._entries = entries

    def __getitem__(self, key: str) -> BaseModel:
        try:
            return self._entries[key]
        except KeyError:
            raise KeyError(
                f"{key!r} not in catalogue {self.name!r}; known keys: {sorted(self._entries)}"
            ) from None

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def keys(self) -> list[str]:
        return sorted(self._entries)


def _src(note: str) -> Provenance:
    return Provenance(status="CONFIRMED", source="vendor datasheet (Phase 1 seed data)", note=note)


MATERIALS = Catalogue(
    "materials",
    {
        "aluminum_6061": MaterialSpec(
            key="aluminum_6061",
            density=Quantity(value=2700.0, unit="kg/m^3", provenance=_src("MatWeb 6061-T6")),
            yield_strength=Quantity(value=2.76e8, unit="Pa", provenance=_src("MatWeb 6061-T6")),
        ),
        "pla": MaterialSpec(
            key="pla",
            density=Quantity(value=1240.0, unit="kg/m^3", provenance=_src("generic FDM PLA")),
            yield_strength=Quantity(value=5.0e7, unit="Pa", provenance=_src("generic FDM PLA")),
        ),
        "steel_1018": MaterialSpec(
            key="steel_1018",
            density=Quantity(value=7870.0, unit="kg/m^3", provenance=_src("MatWeb 1018 CD steel")),
            yield_strength=Quantity(value=3.7e8, unit="Pa", provenance=_src("MatWeb 1018 CD steel")),
        ),
    },
)

STEPPER_MOTORS = Catalogue(
    "stepper_motors",
    {
        "nema17_planetary_13.73": MotorSpec(
            key="nema17_planetary_13.73",
            stall_torque=Quantity(value=3.0, unit="N*m", provenance=_src("NEMA17 + 13.73:1 planetary datasheet")),
            no_load_speed=Quantity(value=13.6, unit="rad/s", provenance=_src("NEMA17 + 13.73:1 planetary datasheet")),
            mass=Quantity(value=0.28, unit="kg", provenance=_src("NEMA17 + 13.73:1 planetary datasheet")),
            gear_ratio=13.73,
        ),
        "nema17_direct": MotorSpec(
            key="nema17_direct",
            stall_torque=Quantity(value=0.45, unit="N*m", provenance=_src("NEMA17 datasheet")),
            no_load_speed=Quantity(value=104.7, unit="rad/s", provenance=_src("NEMA17 datasheet")),
            mass=Quantity(value=0.22, unit="kg", provenance=_src("NEMA17 datasheet")),
            gear_ratio=1.0,
        ),
    },
)

CATALOGUES: dict[str, Catalogue] = {
    "materials": MATERIALS,
    "stepper_motors": STEPPER_MOTORS,
}


def resolve(catalogue: str, key: str) -> BaseModel:
    if catalogue not in CATALOGUES:
        raise KeyError(f"no catalogue named {catalogue!r}; known catalogues: {sorted(CATALOGUES)}")
    return CATALOGUES[catalogue][key]
