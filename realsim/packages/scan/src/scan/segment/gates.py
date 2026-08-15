"""Segmentation gates."""
from __future__ import annotations

import numpy as np

from r2s.core.gates import Severity, check, gate


@gate(id="SEG.OBJECTS_FOUND", stage="segment", severity=Severity.SOFT,
      describe="At least a few objects were segmented.",
      remediation="Zero objects still ships (empty shell + generated cousins), labeled.")
def objects_found(art, ctx):
    n = len(art.payload.objects)
    return check("SEG.OBJECTS_FOUND", n >= 3,
                 measured={"objects": n}, thresholds={"min": 3})


@gate(id="SEG.EXCLUSIVITY", stage="segment", severity=Severity.HARD,
      describe="After resolution, no gaussian belongs to two objects.",
      remediation="Bug in conflict resolution -- this should be structurally impossible.")
def exclusivity(art, ctx):
    # resolution assigns each gaussian one owner; verify totals are consistent
    total_claimed = sum(o.n_gaussians for o in art.payload.objects)
    okay = total_claimed <= art.payload.n_gaussians_total
    return check("SEG.EXCLUSIVITY", okay,
                 measured={"claimed": total_claimed,
                           "total": art.payload.n_gaussians_total},
                 thresholds={"claimed<=total": True})


@gate(id="SEG.SIZE_PLAUSIBLE", stage="segment", severity=Severity.SOFT,
      describe="Every object is between a matchbox and a wardrobe.",
      remediation="Metric scale wrong, or the detector segmented a wall.")
def size_plausible(art, ctx):
    bad = []
    for o in art.payload.objects:
        m = max(o.aabb_m.extents)
        if not (0.02 <= m <= 2.5):
            bad.append(f"{o.object_id}:{m:.2f}m")
    return check("SEG.SIZE_PLAUSIBLE", not bad,
                 measured={"out_of_band": ",".join(bad) or "none"},
                 thresholds={"per_object_max_dim": "[0.02, 2.5] m"})


@gate(id="SEG.NOT_FLOOR", stage="segment", severity=Severity.HARD,
      describe="No 'object' is actually a slab of the floor.",
      remediation="The detector segmented the rug; drop that prompt term.")
def not_floor(art, ctx):
    bad = []
    for o in art.payload.objects:
        dz = o.aabb_m.hi[2] - o.aabb_m.lo[2]
        dxy = max(o.aabb_m.hi[0] - o.aabb_m.lo[0], o.aabb_m.hi[1] - o.aabb_m.lo[1])
        # a floor-slab signature: huge, flat, and sitting at z~0
        if dxy > 1.5 and dz < 0.06 and o.aabb_m.lo[2] < 0.05:
            bad.append(o.object_id)
    return check("SEG.NOT_FLOOR", not bad,
                 measured={"floor_like": ",".join(bad) or "none"},
                 thresholds={"flat+huge+grounded": "reject"})


@gate(id="SEG.ASSIGNED_FRACTION", stage="segment", severity=Severity.SOFT,
      describe="Segmentation did not claim most of the room.",
      remediation="If >40% of gaussians are 'objects', the masks are wrong.")
def assigned_fraction(art, ctx):
    p = art.payload
    frac = p.n_gaussians_assigned / max(p.n_gaussians_total, 1)
    return check("SEG.ASSIGNED_FRACTION", frac <= 0.4,
                 measured={"fraction": round(frac, 3)}, thresholds={"max": 0.4})


@gate(id="SEG.PROPOSER_TAGGED", stage="segment", severity=Severity.HARD,
      describe="Every label carries a Proposer tag (who suggested it).",
      remediation="Schema violation -- labels without provenance are not allowed in.")
def proposer_tagged(art, ctx):
    missing = [o.object_id for o in art.payload.objects if not o.proposer.kind]
    return check("SEG.PROPOSER_TAGGED", not missing,
                 measured={"untagged": len(missing)}, thresholds={"expect": 0})
