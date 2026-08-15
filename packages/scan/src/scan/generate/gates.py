"""Generation gates: the library must actually fuel the identity axis."""
from __future__ import annotations

from r2s.core.gates import Severity, check, gate


@gate(id="GEN.POOL_DEPTH", stage="generate", severity=Severity.SOFT,
      describe="Each scanned category has >=2 substitute candidates (top-K pool).",
      remediation="Top-1 pools silently collapse the identity axis into pose jitter.")
def pool_depth(art, ctx):
    by_cat: dict[str, int] = {}
    for a in art.payload.assets.values():
        by_cat[a.category] = by_cat.get(a.category, 0) + 1
    thin = [f"{c}:{n}" for c, n in sorted(by_cat.items()) if n < 2]
    return check("GEN.POOL_DEPTH", not thin,
                 measured={"thin_categories": ",".join(thin) or "none",
                           "total": len(art.payload.assets)},
                 thresholds={"min_per_category": 2})


@gate(id="GEN.GRASPABLE_POOL", stage="generate", severity=Severity.HARD,
      describe="Every manipulable candidate that survived passes the SO-101 filter.",
      remediation="The index-time filter must run at generation, not at cousin-gate time.")
def graspable_pool(art, ctx):
    from .stage import MANIPULABLE

    bad = [a.asset_id for a in art.payload.assets.values()
           if a.category in MANIPULABLE and not a.fits_so101_gripper]
    return check("GEN.GRASPABLE_POOL", not bad,
                 measured={"ungraspable_survivors": ",".join(bad) or "none"},
                 thresholds={"expect": 0})


@gate(id="GEN.CACHE_KEYED", stage="generate", severity=Severity.HARD,
      describe="Every GENERATED asset records its generation cache key.",
      remediation="Without the key, 'same seed, same scenes' is false -- diffusion "
                  "is not reproducible; the cache is the determinism mechanism.")
def cache_keyed(art, ctx):
    from r2s.core import AssetProvenance

    bad = [a.asset_id for a in art.payload.assets.values()
           if a.provenance == AssetProvenance.GENERATED and not a.generation_cache_key]
    return check("GEN.CACHE_KEYED", not bad,
                 measured={"unkeyed": ",".join(bad) or "none"}, thresholds={"expect": 0})
