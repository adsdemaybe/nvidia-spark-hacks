# SO-101 — vendored open-source CAD

Upstream: **[TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100)**
(the SO-101 lives in the same repository as the deprecated SO-100).
Licence: **Apache-2.0** — see `LICENSE`, copied from upstream.

Designed by The Robot Studio with Hugging Face. `designs/so101_arm.py` imports
these files; nothing here is edited, and nothing here is authored by this project.

## What is here, and why each format

    step/     per-part STEP solids — the geometry the IR actually builds from
    sim/      so101_new_calib.urdf — topology, joint axes, limits, part placement
    assets/   the two servo STLs, used only to locate the servo's own origin

The URDF names STL meshes; we build from the STEP files instead, because a STEP
is a B-rep solid and a mesh is not. Volume, centre of mass and the full inertia
tensor then come out of OpenCascade rather than being estimated from a triangle
soup.

**The two exports are coincident, and that was verified rather than assumed.**
`Upper_arm_SO101.step` spans [-65.085, 0, -35.6]..[77.085, 24.5, 31.7] mm, and
`upper_arm_so101_v1.stl` spans exactly that in metres. So the URDF's placements
apply to the STEP parts unchanged. If a future upstream drop breaks that, the
sha256 pins in `designs/so101_arm.ir.json` will fail first, which is the point
of them.

## The rule this does not break

The prototype's standing rule is that **vendor CAD is for visuals only and must
never be the source of a mating feature** — bolt patterns and bores come from
catalogue constants, because vendor models have been measured wrong in this
project before.

That rule is about *component* CAD: a motor manufacturer's convenience model of
their own part. SO-101 is not that. It is a first-class open-source robot design,
and these files are its definition rather than a depiction of it. What is taken
from them is geometry, mass and placement. No bolt pattern or bore is cut against
them, and the STS3215's own numbers still come from the catalogue, not from here.

## Not vendored

`SO101 Assembly.step` (20 MB) — the whole-arm assembly. The per-part files carry
the same geometry and the URDF carries the structure, so the assembly would be a
third copy of both.
