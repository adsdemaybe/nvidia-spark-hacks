"""Two implementations of the same number, compared (§3, v3).

    "Total mass and CoM are computed independently by the CAD layer (B-rep
    integration) and by the assembled MuJoCo model (sum over bodies).
    Disagreement beyond tolerance is a **pipeline bug filed automatically**, not
    a design finding — the same two-implementations rule `pcb-ai` applies to DRC."

The distinction the last clause makes is the whole value of this module, and it
is easy to lose. When the CAD layer says the robot masses 2.4 kg and the
simulator says 1.9 kg, *the design is not wrong*. Something in the translation
is. Reporting that as a failing criterion sends a design agent off to make the
chassis lighter, chasing a number that was never about the chassis — and the
agent will succeed, because 1.9 and 2.4 both move when you thin a wall, so the
"fix" will appear to work while the bug survives.

So a disagreement here is a `PipelineBug`: it goes to whoever maintains the
translation, it blocks the verdict, and it is never handed to the designer as
something to optimise.

Why the two implementations genuinely are independent, which is what makes the
comparison worth anything: the CAD side integrates over B-rep solids in
OpenCascade and sums; the simulator side compiles URDF into MJCF, applies its
own frame conventions, welds what it welds, and reports `body_mass`/`body_ipos`
from the assembled model. A bug in the exporter shows up in exactly one of them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.ir import RobotIR
from engine.kinematics import link_frames, link_geometry_transform
from engine.mass_properties import MassProperties

# 1% of total mass. Both sides integrate the same solids, so agreement should be
# to floating-point noise; a percent is slack for MuJoCo's own rounding when it
# writes and re-reads inertials, and anything larger is structural.
MASS_TOLERANCE = 0.01
# 1 mm of centre-of-mass disagreement. Absolute rather than relative because a
# CoM offset does not scale with the robot — a 1 mm error is the same 1 mm on a
# tabletop arm and a rover, and on the arm it is the difference between a
# static-margin PASS and a FAIL.
COM_TOLERANCE_M = 0.001


@dataclass(frozen=True)
class PipelineBug:
    """A disagreement between two implementations of one quantity.

    Not a criterion result, deliberately: it has no magnitude to optimise and no
    design change can fix it. `subsystem` is who owns it, which is the field
    that makes it routable rather than merely alarming.
    """

    subsystem: str  # "cad->mjcf", "cad->urdf", ...
    quantity: str  # "total_mass", "centre_of_mass"
    detail: str
    measured_a: float
    measured_b: float
    tolerance: float
    unit: str

    @property
    def disagreement(self) -> float:
        return abs(self.measured_a - self.measured_b)

    def __str__(self) -> str:
        return (
            f"[{self.subsystem}] {self.quantity}: {self.measured_a:.6g} vs "
            f"{self.measured_b:.6g} {self.unit} "
            f"(tolerance {self.tolerance:.6g}) — {self.detail}"
        )


def cad_mass_and_com(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> tuple[float, np.ndarray]:
    """Total mass and world-frame CoM, integrated over the B-rep solids.

    The CoM is assembled through the same kinematic walk the criteria use, so
    this is the CAD layer's own answer end to end — not a second opinion that
    quietly shares the simulator's frame conventions.
    """
    frames = link_frames(ir)
    total = 0.0
    moment = np.zeros(3)
    for link in ir.links:
        mp = mass_props[link.id]
        transform = link_geometry_transform(ir, link.id, frames)
        world_com = transform[:3, :3] @ np.array(mp.com.as_tuple()) + transform[:3, 3]
        total += mp.mass
        moment += mp.mass * world_com
    com = moment / total if total > 0 else np.zeros(3)
    return total, com


def simulated_mass_and_com(model) -> tuple[float, np.ndarray]:
    """Total mass and CoM read back out of the compiled MuJoCo model.

    Read from `body_mass` and the body inertial frames at the model's reference
    configuration, which is what MuJoCo will actually integrate — not from the
    XML we handed it. A model that compiled with a body silently dropped reports
    it here.
    """
    import mujoco

    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    masses = np.asarray(model.body_mass, dtype=float)
    positions = np.asarray(data.xipos, dtype=float)  # world CoM of each body
    # Body 0 is the world body: mass 0, and including it is harmless, but being
    # explicit stops a future MuJoCo that gives it mass from poisoning this.
    masses = masses[1:]
    positions = positions[1:]
    total = float(masses.sum())
    com = (masses[:, None] * positions).sum(axis=0) / total if total > 0 else np.zeros(3)
    return total, com


def against_simulation(
    ir: RobotIR, mass_props: dict[str, MassProperties], model=None
) -> list[PipelineBug]:
    """Compare the CAD mass model against the compiled simulation model.

    Returns an empty list when they agree, and — importantly — when the
    comparison cannot be made at all. A fixed-base robot has its root welded to
    the world and MuJoCo drops that body's mass, so the two totals are supposed
    to differ; comparing them anyway would file a bug on every bench arm forever.
    `sim_loads` in tier 2 already checks the welded case against the right
    expectation, and this defers to it rather than duplicating it badly.
    """
    if ir.base != "floating":
        return []

    if model is None:
        try:
            from engine.sim.mujoco_harness import compile_floating
        except ImportError:
            return []  # no simulator here; tier 2 already reports that it did not run
        try:
            model = compile_floating(ir)
        except Exception:
            return []  # a model that will not compile is `sim_loads`'s finding, not a bug here

    cad_mass, cad_com = cad_mass_and_com(ir, mass_props)
    sim_mass, sim_com = simulated_mass_and_com(model)

    bugs: list[PipelineBug] = []

    if cad_mass > 0:
        mass_error = abs(cad_mass - sim_mass) / cad_mass
        if mass_error > MASS_TOLERANCE:
            bugs.append(
                PipelineBug(
                    subsystem="cad->mjcf",
                    quantity="total_mass",
                    detail=(
                        f"the B-rep integration and the compiled model disagree by "
                        f"{mass_error * 100:.2f}%. This is a translation defect, not a "
                        "design one — a link whose inertial was dropped or a body "
                        "MuJoCo welded. Do not lighten the design to close it."
                    ),
                    measured_a=cad_mass,
                    measured_b=sim_mass,
                    tolerance=MASS_TOLERANCE * cad_mass,
                    unit="kg",
                )
            )

    com_error = float(np.linalg.norm(cad_com - sim_com))
    if com_error > COM_TOLERANCE_M:
        bugs.append(
            PipelineBug(
                subsystem="cad->mjcf",
                quantity="centre_of_mass",
                detail=(
                    f"CoM differs by {com_error * 1000:.2f}mm between the CAD assembly "
                    f"({cad_com[0]:.4f}, {cad_com[1]:.4f}, {cad_com[2]:.4f}) and the "
                    f"compiled model ({sim_com[0]:.4f}, {sim_com[1]:.4f}, {sim_com[2]:.4f}). "
                    "Almost always a frame convention: a joint origin applied twice, or "
                    "an inertial expressed about the link origin where the exporter "
                    "expected the centre of mass."
                ),
                measured_a=0.0,
                measured_b=com_error,
                tolerance=COM_TOLERANCE_M,
                unit="m",
            )
        )

    return bugs
