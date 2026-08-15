"""Emit a CAD robot as JSON, for `full_stack.py` to read.

This runs **in the CAD project's interpreter**, not cosim's, and communicates over stdout.
That is not incidental convenience — it is the contract both plans state:

    cosim/src/cosim/robot.py:  "Never import their Python. Adapters take JSON."
    text-to-cad-plan §7:       "subprocess over a file contract, never imported"

and it is load-bearing for a reason the venvs make concrete: `build123d`/OCP lives in the
CAD environment and `mujoco` in cosim's. Importing across that line means one environment
has to satisfy both projects' dependency graphs forever.

Mass properties are computed *here*, on the CAD side, because that is the side that owns
them — they come from its solids and its material densities via OpenCascade. cosim
receives numbers it cannot second-guess, which is the point: a bounding-box estimate made
on the far side would be a second opinion wearing the authority of the first.

    <cad-venv>/bin/python _cad_export.py simple_rover
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: _cad_export.py <robot-name> [engine-src-dir]", file=sys.stderr)
        return 2
    name = argv[1]
    src = argv[2] if len(argv) > 2 else "../cad-generation/engine/src"
    sys.path.insert(0, str(Path(src).resolve()))

    from engine import examples
    from engine.geometry import registry

    factory = getattr(examples, name, None)
    if factory is None:
        available = [n for n in dir(examples) if not n.startswith("_") and callable(getattr(examples, n))]
        print(json.dumps({"error": f"no robot {name!r}", "available": available}))
        return 1

    ir = factory()
    payload = ir.model_dump(mode="json")

    mass_by_link: dict[str, dict] = {}
    failures: dict[str, str] = {}
    for link in ir.links:
        try:
            mp = registry.build(link.geometry).mass_properties
        except Exception as exc:  # noqa: BLE001
            # Report rather than substitute. A link whose solid will not build has no
            # mass, and inventing one here would hide a geometry bug behind a plausible
            # number that every downstream torque margin would then trust.
            failures[link.id] = f"{type(exc).__name__}: {exc}"
            continue
        mass_by_link[link.id] = {
            "mass": mp.mass,
            "volume": mp.volume,
            "com": {"x": mp.com.x, "y": mp.com.y, "z": mp.com.z},
            "bbox_size": {"x": mp.bbox_size.x, "y": mp.bbox_size.y, "z": mp.bbox_size.z},
            "inertia": {"ixx": mp.inertia.ixx, "iyy": mp.inertia.iyy, "izz": mp.inertia.izz},
        }

    print(json.dumps({"robot": payload, "mass_by_link": mass_by_link, "failures": failures}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
