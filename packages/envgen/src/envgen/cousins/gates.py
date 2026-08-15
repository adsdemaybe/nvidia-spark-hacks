"""Cousin-set gates. Per-candidate rejection happens inside the generator's
retry loops (cheap->expensive, short-circuiting); these stage gates audit the
ACCEPTED SET -- the properties that make 8 scenes a training distribution
rather than 8 renders of one scene."""
from __future__ import annotations

import json

from r2s.core.gates import Severity, check, gate


@gate(id="SCENE.COUNT", stage="cousins", severity=Severity.HARD,
      describe="Exactly the requested number of scenes was emitted.",
      remediation="The generator must fail loudly, never ship N-1 silently; "
                  "if this fires, read the GenerationFailure in the logs.")
def scene_count(art, ctx):
    n = len(art.payload.scene_ids)
    want = getattr(ctx, "extra", {}).get("n_scenes", 8) if ctx else 8
    return check("SCENE.COUNT", n == want,
                 measured={"emitted": n}, thresholds={"expected": want})


@gate(id="SCENE.ACCOUNTING", stage="cousins", severity=Severity.HARD,
      describe="accepted + rejected == attempts; no attempt vanished.",
      remediation="A silent attempt is exactly the bug class this exists to catch.")
def accounting(art, ctx):
    p = art.payload
    return check("SCENE.ACCOUNTING", p.accepted + p.rejected == p.attempts,
                 measured={"accepted": p.accepted, "rejected": p.rejected,
                           "attempts": p.attempts},
                 thresholds={"identity": "accepted+rejected==attempts"})


@gate(id="SCENE.HELDOUT_EXISTS", stage="cousins", severity=Severity.HARD,
      describe="The set contains held-out scenes the policy never trains on.",
      remediation="Without holdout, the cousin hypothesis is untestable.")
def heldout(art, ctx):
    n = len(art.payload.heldout_ids)
    return check("SCENE.HELDOUT_EXISTS", n >= 1,
                 measured={"heldout": n}, thresholds={"min": 1})


@gate(id="SCENE.TWIN_EXISTS", stage="cousins", severity=Severity.SOFT,
      describe="Exactly one TWIN anchors the set (eval anchor + F5 overlay).",
      remediation="c0 should be the twin; check the stratification table.")
def twin(art, ctx):
    ids = art.payload.scene_ids
    return check("SCENE.TWIN_EXISTS", "c0" in ids,
                 measured={"ids": ",".join(ids)}, thresholds={"expect": "c0 present"})


@gate(id="SCENE.REJECTIONS_LOGGED", stage="cousins", severity=Severity.HARD,
      describe="The rejection ledger artifact exists and parses.",
      remediation="Rejections must be visible; a missing ledger hides sampler pathology.")
def rejections_logged(art, ctx):
    ref = art.payload.rejections_ref
    if ref is None:
        return check("SCENE.REJECTIONS_LOGGED", False,
                     measured={"ledger": "missing"}, thresholds={"require": "present"})
    try:
        raw = ctx.store.get_bytes(ref).decode()
        for line in raw.splitlines():
            if line.strip():
                json.loads(line)
        okay = True
    except Exception:
        okay = False
    return check("SCENE.REJECTIONS_LOGGED", okay,
                 measured={"parses": okay}, thresholds={"require": "valid ndjson"})
