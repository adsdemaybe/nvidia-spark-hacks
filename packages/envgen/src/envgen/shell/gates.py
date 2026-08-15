"""Shell gates."""
from __future__ import annotations

import numpy as np

from r2s.core.gates import Severity, check, gate


@gate(id="SHELL.FLOOR_FOUND", stage="shell", severity=Severity.HARD,
      describe="A floor plane exists and is level.",
      remediation="Reconstruction's gravity alignment failed; inspect the PLY.")
def floor_found(art, ctx):
    f = art.payload.floor
    tilt = float(np.degrees(np.arccos(np.clip(f.normal[2], -1, 1))))
    return check("SHELL.FLOOR_FOUND", tilt <= 5.0,
                 measured={"tilt_deg": round(tilt, 2)}, thresholds={"max": 5.0})


@gate(id="SHELL.ROOM_AREA", stage="shell", severity=Severity.HARD,
      describe="The room footprint is between a closet and a warehouse.",
      remediation="Scale or footprint extraction broke.")
def room_area(art, ctx):
    a = art.payload.room_area_m2
    return check("SHELL.ROOM_AREA", 1.0 <= a <= 120.0,
                 measured={"area_m2": round(a, 2)}, thresholds={"band": "[1, 120]"})


@gate(id="SHELL.GAUSSIANS_REMAIN", stage="shell", severity=Severity.HARD,
      describe="Most of the cloud survived object stripping.",
      remediation="If segmentation claimed >40% of the room, its masks were wrong "
                  "(SEG.ASSIGNED_FRACTION should have warned).")
def gaussians_remain(art, ctx):
    p = art.payload
    total = p.n_gaussians_kept + p.n_gaussians_removed
    frac = p.n_gaussians_kept / max(total, 1)
    return check("SHELL.GAUSSIANS_REMAIN", frac >= 0.6,
                 measured={"kept_fraction": round(frac, 3)}, thresholds={"min": 0.6})


@gate(id="SHELL.PLACEABLE_SURFACE", stage="shell", severity=Severity.HARD,
      describe="There is at least one placeable surface with real area.",
      remediation="No floor polygon means nothing can ever be placed.")
def placeable(art, ctx):
    best = max((s.area_m2 for s in art.payload.placeable_surfaces), default=0.0)
    return check("SHELL.PLACEABLE_SURFACE", best >= 0.25,
                 measured={"largest_m2": round(best, 3)}, thresholds={"min": 0.25})


@gate(id="SHELL.HOLES_RECORDED", stage="shell", severity=Severity.HARD,
      describe="Every removed object left an explicit Hole record.",
      remediation="Holes are data; losing one hides a visual artifact from the "
                  "occlusion bias in the composer.")
def holes_recorded(art, ctx):
    p = art.payload
    n_removed_objs = len(p.holes)
    return check(
        "SHELL.HOLES_RECORDED",
        p.n_gaussians_removed == sum(h.n_gaussians_removed for h in p.holes),
        measured={"holes": n_removed_objs, "gaussians_removed": p.n_gaussians_removed},
        thresholds={"accounting": "removed == sum(holes)"},
    )
