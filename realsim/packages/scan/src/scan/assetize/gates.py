"""Assetize gates: per-asset failures drop the asset (visible in `dropped`);
these bundle-level gates check what survived."""
from __future__ import annotations

from r2s.core.gates import Severity, check, gate
from r2s.core.provenance import ValueProvenance


@gate(id="ASSET.SOME_SURVIVED", stage="assetize", severity=Severity.SOFT,
      describe="At least one asset survived assetization.",
      remediation="Zero assets still ships (generated cousins); inspect `dropped`.")
def some_survived(art, ctx):
    n = len(art.payload.assets)
    return check("ASSET.SOME_SURVIVED", n >= 1,
                 measured={"assets": n, "dropped": len(art.payload.dropped)},
                 thresholds={"min": 1})


@gate(id="ASSET.HULLS_CLOSED", stage="assetize", severity=Severity.HARD,
      describe="Every surviving asset's collision hulls are closed convex solids.",
      remediation="A non-watertight hull explodes PhysX/MuJoCo; the decomposer must "
                  "convex-hull every part.")
def hulls_closed(art, ctx):
    bad = [a.asset_id for a in art.payload.assets.values() if not a.collision.watertight]
    return check("ASSET.HULLS_CLOSED", not bad,
                 measured={"open": ",".join(bad) or "none"}, thresholds={"expect": "all closed"})


@gate(id="ASSET.MASS_RANGE", stage="assetize", severity=Severity.HARD,
      describe="Every mass is between 5g and 100kg.",
      remediation="A mass outside this band means the scale or density prior broke.")
def mass_range(art, ctx):
    bad = []
    for a in art.payload.assets.values():
        m = a.physics.mass.value
        if not (0.005 <= m <= 100.0):
            bad.append(f"{a.asset_id}:{m:.4f}kg")
    return check("ASSET.MASS_RANGE", not bad,
                 measured={"out_of_band": ",".join(bad) or "none"},
                 thresholds={"band": "[0.005, 100] kg"})


@gate(id="ASSET.HULL_FIDELITY", stage="assetize", severity=Severity.SOFT,
      describe="Hull volume does not wildly exceed the mesh volume.",
      remediation=">1.6x means collision is much fatter than the object; contacts lie.")
def hull_fidelity(art, ctx):
    bad = [f"{a.asset_id}:{a.collision.volume_ratio:.2f}"
           for a in art.payload.assets.values() if a.collision.volume_ratio > 1.6]
    return check("ASSET.HULL_FIDELITY", not bad,
                 measured={"over": ",".join(bad) or "none"}, thresholds={"max_ratio": 1.6})


@gate(id="ASSET.PROVENANCE_COMPLETE", stage="assetize", severity=Severity.HARD,
      describe="No physical constant claims CONFIRMED without a source.",
      remediation="Schema enforces this at construction; failure here means bypass.")
def provenance_complete(art, ctx):
    bad = []
    for a in art.payload.assets.values():
        for f in (a.physics.mass, a.physics.density, a.physics.static_friction):
            if f.provenance in (ValueProvenance.CONFIRMED, ValueProvenance.MEASURED) and not f.source:
                bad.append(a.asset_id)
    return check("ASSET.PROVENANCE_COMPLETE", not bad,
                 measured={"violations": len(bad)}, thresholds={"expect": 0})


@gate(id="ASSET.STABLE_POSE", stage="assetize", severity=Severity.SOFT,
      describe="Every asset has at least one computed stable pose.",
      remediation="Fallback identity pose means the placement sampler guesses orientation.")
def stable_pose(art, ctx):
    bad = [a.asset_id for a in art.payload.assets.values() if not a.stable_poses]
    return check("ASSET.STABLE_POSE", not bad,
                 measured={"missing": ",".join(bad) or "none"}, thresholds={"min_poses": 1})
