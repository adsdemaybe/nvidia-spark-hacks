"""TRELLIS -> Omniverse asset converter -> LLM-proposed PhysX properties.

The user-chosen production chain for generated assets (Spark):

  1. TRELLIS text/image->3D produces mesh.glb
  2. `omni.kit.asset_converter` (inside the sim container's Kit Python -- the
     converter is an Omniverse extension, NOT a pip package) converts GLB->USD
  3. an LLM proposes PhysX properties for the converted prim

Invariant, enforced not hoped: the LLM PROPOSES. Every proposed number lands as
Measured(provenance=INFERRED, method="llm_physx_proposal", ...) tagged with
Proposer(kind="llm"), is clamped to the category's plausible band, and then the
SAME deterministic gates that judge density-prior physics judge these:
ASSET.MASS_RANGE, inertia validity, and ultimately the MuJoCo settle. A wrong
LLM guess fails a gate; it cannot ship.

Blind-authored for the Spark. Everything here trusts files on disk, never exit
flags, and each sub-step is independently replayable.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import trimesh

from r2s.core import Measured, Proposer, ValueProvenance

# plausible-band clamps per category: the LLM proposes INSIDE these or gets clamped.
_BANDS = {  # category -> (mass_lo, mass_hi, mu_lo, mu_hi)
    "mug": (0.15, 0.6, 0.3, 0.9),
    "bottle": (0.02, 1.5, 0.2, 0.7),
    "box": (0.05, 5.0, 0.3, 0.8),
    "chair": (2.0, 12.0, 0.3, 0.8),
    "table": (5.0, 40.0, 0.3, 0.8),
}
_DEFAULT_BAND = (0.02, 20.0, 0.2, 0.9)


class TrellisSource:
    """Rung 1: text -> mesh via a local TRELLIS checkout."""

    name = "trellis"

    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("TRELLIS_ROOT", "/opt/trellis"))

    def available(self) -> tuple[bool, str]:
        return (self.root / "run.py").exists() or (self.root / "app.py").exists(), str(self.root)

    def produce(self, category: str, descriptor: str, seed: int, cache_dir: Path) -> trimesh.Trimesh:
        from .sources import cache_key

        key = cache_key(self.name, category, descriptor, seed)
        cached = cache_dir / f"{key}.glb"
        if cached.exists():
            return trimesh.load(cached, force="mesh")
        out = cache_dir / f"{key}_raw.glb"
        script = self.root / ("run.py" if (self.root / "run.py").exists() else "app.py")
        proc = subprocess.run(
            ["python", str(script), "--prompt", descriptor or category,
             "--seed", str(seed), "--output", str(out), "--format", "glb"],
            cwd=self.root, capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"trellis failed (rc={proc.returncode}): {proc.stderr[-400:]}")
        mesh = trimesh.load(out, force="mesh")
        mesh.export(cached)
        return mesh


def convert_glb_to_usd(glb: Path, out_usd: Path, sim_image: str = "realsim/sim:dev") -> Path:
    """Rung 2: omni.kit.asset_converter, inside the sim container's Kit Python.

    The converter callback API is async; the entry script below runs it to
    completion and exits nonzero on failure. We assert on the output FILE."""
    entry = out_usd.parent / "_convert_entry.py"
    entry.write_text(
        "import asyncio, sys\n"
        "import omni.kit.asset_converter as conv\n"
        "async def main(src, dst):\n"
        "    task = conv.get_instance().create_converter_task(src, dst, None)\n"
        "    ok = await task.wait_until_finished()\n"
        "    sys.exit(0 if ok else 3)\n"
        "asyncio.get_event_loop().run_until_complete(main(sys.argv[1], sys.argv[2]))\n"
    )
    proc = subprocess.run(
        ["docker", "run", "--rm", "--gpus", "all",
         "-v", f"{glb.parent}:/in", "-v", f"{out_usd.parent}:/out",
         sim_image, "bash", "-lc",
         f"$ISAACSIM_PATH/python.sh /out/{entry.name} /in/{glb.name} /out/{out_usd.name}"],
        capture_output=True, text=True, timeout=900,
    )
    if not out_usd.exists():
        raise RuntimeError(
            f"asset converter produced no USD (rc={proc.returncode}): {proc.stderr[-400:]}"
        )
    return out_usd


def propose_physx_properties(
    category: str, descriptor: str, bbox_m: tuple[float, float, float],
    llm_call=None,
) -> dict[str, Measured]:
    """Rung 3: LLM proposes; bands clamp; gates dispose.

    `llm_call(prompt) -> json str` is injected (r2s-core has no LLM client, and
    this module only runs when the caller wires one). With llm_call=None the
    deterministic density-prior path in assetize/physics.py is used instead --
    this function simply is not called.
    """
    prompt = (
        "Estimate physical properties for a rigid object in a physics simulator.\n"
        f"category: {category}\ndescription: {descriptor}\n"
        f"bounding box (m): {bbox_m}\n"
        'Reply with ONLY a JSON object: {"mass_kg": float, "static_friction": float, '
        '"restitution": float, "material": str}'
    )
    raw = llm_call(prompt)
    data = json.loads(raw)

    lo_m, hi_m, lo_mu, hi_mu = _BANDS.get(category, _DEFAULT_BAND)
    proposer = Proposer(kind="llm", detail="physx_property_proposal")
    mass = float(np.clip(float(data["mass_kg"]), lo_m, hi_m))
    mu = float(np.clip(float(data["static_friction"]), lo_mu, hi_mu))
    rest = float(np.clip(float(data.get("restitution", 0.1)), 0.0, 0.6))
    method = f"llm_physx_proposal clamped_to[{lo_m},{hi_m}]kg by {proposer.detail}"
    return {
        "mass": Measured[float](value=mass, unit="kg",
                                provenance=ValueProvenance.INFERRED, method=method),
        "static_friction": Measured[float](value=mu, unit="1",
                                           provenance=ValueProvenance.INFERRED, method=method),
        "restitution": Measured[float](value=rest, unit="1",
                                       provenance=ValueProvenance.INFERRED, method=method),
        "_material": data.get("material", "unknown"),  # type: ignore[dict-item]
    }
