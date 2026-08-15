"""Where 3D models come from, and what they may be used for (§6.2, §5).

The ingest pipeline, in order, and none of it optional:

    fetch -> hash -> license recorded -> units and axes normalized
          -> watertightness/scale sanity -> mass cross-check -> tag -> cache

The mass cross-check is the step that earns the rest of it. A downloaded motor
model whose volume times the catalogue density lands 40% off the datasheet mass
is mis-scaled or hollow — a millimetre model read as metres, a shelled body
somebody exported for rendering. Caught at ingest it is one quarantined file;
missed, it skews every centre of mass downstream, and the resulting static
margin is wrong in a way no criterion can see, because every criterion is
reading the same bad number.

And the rule the whole thing exists to enforce, from §5:

    "Vendor CAD is for visuals only — never cut a mating feature from downloaded
    CAD; bolt patterns and shaft diameters come from datasheet constants. §6
    operationalizes this: fetched models are tagged at ingest and the CAD layer
    *refuses* boolean operations against any solid tagged `visual_only`. The rule
    stops being discipline and becomes a type error."

`require_matable()` is that refusal. A generator that is about to cut a hole
against a vendored solid calls it and gets a `VisualOnlyError` naming the asset
and what to do instead. Discipline is what you have until somebody is in a
hurry; this is what you have afterwards.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from engine.sourcing.cache import CacheEntry, SourcingCache

# How far the model's implied mass may sit from the datasheet's before the model
# is not describing the same object. 15% is loose on purpose: a real STEP omits
# the wiring, the label and sometimes the shaft, so exact agreement is not the
# expectation. What it catches is the order-of-magnitude class — a 1000x scale
# error, a hollow shell, a model of a different frame size.
MASS_TOLERANCE = 0.15

# Licences that permit a model in a design we might manufacture from. Anything
# else is recorded and quarantined rather than silently used: §10 excludes
# "scraping CAD portals without APIs" as "brittle and license-hostile", and a
# community model with no stated licence is the same problem arriving by a
# politer route.
PERMISSIVE_LICENSES = frozenset(
    {
        "manufacturer-published",
        "CC0-1.0",
        "CC-BY-4.0",
        "MIT",
        "Apache-2.0",
        "BSD-3-Clause",
        "public-domain",
    }
)

_SIDECAR_SUFFIX = ".provenance.json"


class VisualOnlyError(RuntimeError):
    """A boolean operation was attempted against a model tagged visual_only."""


@dataclass
class ModelProvenance:
    """The sidecar written next to every ingested model.

    A file rather than a database row because the asset loader is the thing that
    has to read it, the asset loader has no database, and a model that travels
    without its provenance is a model whose trust level is whatever the next
    person assumes.
    """

    asset: str  # path relative to the asset root
    sha256: str
    source_url: str
    license: str
    supplier: str = ""
    part_number: str = ""
    # The load-bearing field. True for everything fetched, always: §5 allows no
    # exception, and "best available" is still visual_only.
    visual_only: bool = True
    units_normalized_to: str = "mm"
    up_axis: str = "Z"
    origin: str = ""  # what the origin was moved to, e.g. "datasheet datum A"
    mass_check: dict = field(default_factory=dict)
    quarantined: bool = False
    quarantine_reason: str = ""
    note: str = ""

    def write(self, path: Path) -> Path:
        sidecar = path.with_name(path.name + _SIDECAR_SUFFIX)
        sidecar.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        return sidecar

    @classmethod
    def read(cls, path: Path) -> "ModelProvenance | None":
        sidecar = path.with_name(path.name + _SIDECAR_SUFFIX)
        if not sidecar.exists():
            return None
        return cls(**json.loads(sidecar.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class IngestResult:
    """What ingest concluded, whether or not the model survived it."""

    accepted: bool
    provenance: ModelProvenance
    reasons: tuple[str, ...] = ()
    placeholder: dict | None = None  # parametric fallback when the model failed


def check_mass(
    volume_m3: float, density_kg_per_m3: float, datasheet_mass_kg: float
) -> tuple[bool, str, dict]:
    """The §6.2 cross-check: does the model's volume imply the datasheet's mass?

    Returns `(ok, explanation, record)`. The record goes in the sidecar so a
    later reader can see the numbers rather than a verdict.
    """
    implied = volume_m3 * density_kg_per_m3
    if datasheet_mass_kg <= 0:
        return (
            False,
            "the datasheet mass is zero or missing, so there is nothing to check the "
            "model against — the cross-check cannot be waived by omitting its input",
            {"implied_kg": implied, "datasheet_kg": datasheet_mass_kg, "error": None},
        )
    error = (implied - datasheet_mass_kg) / datasheet_mass_kg
    record = {
        "implied_kg": implied,
        "datasheet_kg": datasheet_mass_kg,
        "error": error,
        "tolerance": MASS_TOLERANCE,
        "volume_m3": volume_m3,
        "density_kg_m3": density_kg_per_m3,
    }
    if abs(error) <= MASS_TOLERANCE:
        return True, f"model implies {implied * 1000:.1f}g against {datasheet_mass_kg * 1000:.1f}g listed ({error * 100:+.1f}%)", record

    # Name the two likely causes, because they need different fixes and the
    # numbers distinguish them: a factor of 1e9 is metres-read-as-millimetres,
    # anything else is usually a shelled or decimated body.
    ratio = implied / datasheet_mass_kg
    if ratio > 100 or ratio < 0.01:
        cause = (
            f"the model is off by {ratio:.3g}x, which is a unit error — a millimetre "
            "model read as metres is 1e9, the reverse is 1e-9"
        )
    else:
        cause = (
            "the model is the right order of magnitude but the wrong solid — usually a "
            "shelled body exported for rendering, or a different frame size under the "
            "same part page"
        )
    return (
        False,
        f"model implies {implied * 1000:.1f}g against {datasheet_mass_kg * 1000:.1f}g "
        f"listed ({error * 100:+.1f}%, tolerance +/-{MASS_TOLERANCE * 100:.0f}%): {cause}",
        record,
    )


def placeholder_from_dimensions(
    length_m: float, width_m: float, height_m: float, *, reason: str
) -> dict:
    """A parametric stand-in for a model that failed ingest (§6.2).

    "Failures quarantine the model; the part keeps a parametric placeholder
    (cylinder/box from datasheet dimensions) so the pipeline never blocks on a
    pretty model."

    Returned as `GeometrySpec` params for the `component` generator, which builds
    a solid-box inertia from exactly these three numbers. The placeholder is
    less pretty and strictly more trustworthy: its dimensions come from the
    datasheet, which is where mating dimensions were always supposed to come
    from anyway.
    """
    return {
        "generator": "component",
        "params": {
            "length_m": length_m,
            "width_m": width_m,
            "height_m": height_m,
        },
        "reason": reason,
    }


def ingest_model(
    *,
    asset_path: Path,
    data: bytes,
    source_url: str,
    license: str,
    supplier: str = "",
    part_number: str = "",
    volume_m3: float | None = None,
    density_kg_per_m3: float | None = None,
    datasheet_mass_kg: float | None = None,
    datasheet_dimensions_m: tuple[float, float, float] | None = None,
    units_normalized_to: str = "mm",
    up_axis: str = "Z",
    origin: str = "",
    cache: SourcingCache | None = None,
    fetched_at: str = "",
) -> IngestResult:
    """Run one fetched model through the whole §6.2 pipeline.

    Writes the bytes into the content-addressed cache and the asset tree, writes
    the sidecar, and returns whether the model may be used. A rejected model is
    still written — the evidence is the point — but its sidecar says
    `quarantined` and `require_matable`/`load_step` will refuse it.

    The caller supplies `volume_m3` because measuring it means importing the
    STEP through OpenCascade, and this module deliberately does not depend on
    build123d: ingest runs at catalogue-build time on a machine that may not have
    the CAD stack, and the measurement belongs to the layer that owns geometry.
    """
    cache = cache or SourcingCache()
    reasons: list[str] = []

    digest = cache.put(data)
    cache.record(
        CacheEntry(
            key=f"model:{source_url}",
            sha256=digest,
            media_type="model/step",
            source_url=source_url,
            license=license,
            fetched_at=fetched_at,
            note=f"{supplier} {part_number}".strip(),
        )
    )

    provenance = ModelProvenance(
        asset=asset_path.name,
        sha256=digest,
        source_url=source_url,
        license=license,
        supplier=supplier,
        part_number=part_number,
        visual_only=True,  # always, per §5 — there is no path that sets this False
        units_normalized_to=units_normalized_to,
        up_axis=up_axis,
        origin=origin,
    )

    if license not in PERMISSIVE_LICENSES:
        reasons.append(
            f"licence {license!r} is not in the permitted set. Recorded rather than "
            "assumed permissive: a model we cannot show a licence for is a model we "
            "cannot ship a design containing."
        )

    if volume_m3 is not None and density_kg_per_m3 and datasheet_mass_kg is not None:
        ok, explanation, record = check_mass(volume_m3, density_kg_per_m3, datasheet_mass_kg)
        provenance.mass_check = record
        if not ok:
            reasons.append(f"mass cross-check failed: {explanation}")
        else:
            provenance.note = explanation
    else:
        # Not a failure, but not a pass either. Recorded so that "this model was
        # never cross-checked" is visible rather than being indistinguishable
        # from "this model passed".
        provenance.mass_check = {"skipped": True}
        provenance.note = (
            "mass cross-check skipped — the caller supplied no volume, density or "
            "datasheet mass. This model's scale has not been verified."
        )

    accepted = not reasons
    if not accepted:
        provenance.quarantined = True
        provenance.quarantine_reason = "; ".join(reasons)

    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(data)
    provenance.write(asset_path)

    if not accepted:
        try:
            cache.quarantine(f"model:{source_url}", provenance.quarantine_reason)
        except KeyError:  # pragma: no cover - the record above always exists
            pass

    placeholder = None
    if not accepted and datasheet_dimensions_m is not None:
        placeholder = placeholder_from_dimensions(
            *datasheet_dimensions_m,
            reason=(
                f"{asset_path.name} was quarantined at ingest ({provenance.quarantine_reason}); "
                "using the datasheet envelope so the pipeline is not blocked on a model"
            ),
        )

    return IngestResult(
        accepted=accepted,
        provenance=provenance,
        reasons=tuple(reasons),
        placeholder=placeholder,
    )


def require_matable(asset_path: Path) -> None:
    """Refuse a boolean against a vendor model. §5, as a type error.

    Called by any geometry generator about to cut, fuse or intersect against a
    vendored solid. The message names the alternative, because the alternative
    is always available and is always better: the bolt circle is a number in the
    datasheet, and taking it from there is both correct and cheaper than
    selecting a face on someone else's B-rep.
    """
    provenance = ModelProvenance.read(asset_path)
    if provenance is None:
        # No sidecar means the asset predates ingest, which is most of `vendor/`
        # today. Refusing here would break existing designs to enforce a rule on
        # files that were never claimed to be vendor CAD; the ingest pipeline is
        # what makes the tag exist, and untagged assets are the migration's job.
        return
    if provenance.quarantined:
        raise VisualOnlyError(
            f"{asset_path.name} was quarantined at ingest: {provenance.quarantine_reason}. "
            "It may not be used at all, for booleans or for visuals."
        )
    if provenance.visual_only:
        raise VisualOnlyError(
            f"{asset_path.name} is vendor CAD tagged visual_only (from "
            f"{provenance.source_url or 'an unrecorded source'}), and §5 forbids cutting a "
            "mating feature from it: the model is a picture of the part, and its faces "
            "carry whatever tolerance the vendor's exporter felt like. Take the bolt "
            "pattern, shaft diameter and hole positions from the datasheet as constants "
            "and build the mating geometry parametrically. The vendor solid stays in the "
            "assembly for visuals and for the mass cross-check."
        )
