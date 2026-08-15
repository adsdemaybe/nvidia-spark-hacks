"""M6 — sending a failure to the side that can fix it.

The three-way loop only converges if a failure goes to the right participant. "The joint
is too slow" is the case that matters, because it has two completely different causes and
guessing between them is how a loop thrashes: PCB widens a trace, CAD lightens a link,
neither was the problem, and three rounds are spent discovering that.

**The discriminator is a measurement, not a judgement**, which is the same rule the rest
of this project runs on:

    peak current at the driver's limit  ->  the electronics cannot deliver more  ->  F1
    current well inside limits, still slow ->  the mechanics needs more torque   ->  F2

Physically: current is what makes torque. If the driver is already saturated, no
mechanical change makes it faster — the board has to deliver more. If the driver is
loafing at a third of its rating and the joint still will not move, the electronics are
not the constraint and the gearbox, the mass or the friction are.

The band between those is real and is reported as such rather than resolved by
preference. A joint at 70% of the driver's rating is genuinely both-ish, and saying so
lets a human decide instead of watching the loop oscillate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cosim.gate import DriverLimits, Verdict
from cosim.rollout import RolloutResult

Side = Literal["pcb", "cad", "both", "simulation", "none"]


@dataclass
class Routing:
    side: Side
    reason: str
    evidence: str
    """What the receiving side should actually change."""
    suggestion: str


# Where "saturated" starts and "loafing" ends. Wide on purpose: the middle is a real
# state, and narrowing it to a single threshold would manufacture false confidence about
# which side is at fault.
SATURATED = 0.85
LOAFING = 0.40


def route(
    verdict: Verdict,
    result: RolloutResult,
    limits: DriverLimits,
) -> Routing:
    """Decide which side owns this failure."""

    # A diverged rollout is nobody's design problem. Sending it to either side would have
    # them chase a fault that is not in their design.
    if any(c.name == "coupling stable" and not c.passed for c in verdict.checks):
        return Routing(
            side="simulation",
            reason="the co-simulation diverged, so there is no trajectory to attribute",
            evidence=next(c.detail for c in verdict.checks if c.name == "coupling stable"),
            suggestion="shorten the control period, or fall back to the single-rate motor "
            "model, before drawing any conclusion about the board or the robot",
        )

    if verdict.passed:
        return Routing("none", "the rollout met every gate", "", "nothing to route")

    failing = [c.name for c in verdict.checks if c.blocking and not c.passed]
    headroom = result.peak_current_a / limits.peak_current_a if limits.peak_current_a else 0.0
    current_note = (
        f"peak {result.peak_current_a:.3f} A of a {limits.peak_current_a:.2f} A rating "
        f"({headroom * 100:.0f}% of headroom used)"
    )

    # Over the rating is unambiguous, whatever else failed: the board cannot ship like
    # this even if the robot did its task.
    if "electrical survival" in failing:
        return Routing(
            side="pcb",
            reason="the driver is over its rating",
            evidence=current_note,
            suggestion="a driver with more headroom, or lower the commanded duty; widening "
            "copper will not help because the limit is the silicon, not the trace",
        )

    if any(c.name.startswith("thermal") and not c.passed for c in verdict.checks):
        return Routing(
            side="pcb",
            reason="the driver survives the current but not the heat",
            evidence=next(c.detail for c in verdict.checks if c.name.startswith("thermal")),
            suggestion="more copper into the package — a thermal pad and vias — or a lower "
            "duty cycle. This is a board change, not a motor change",
        )

    if "task" in failing:
        task = next(c for c in verdict.checks if c.name == "task")
        if headroom >= SATURATED:
            return Routing(
                side="pcb",
                reason="the joint missed its target while the driver was already saturated",
                evidence=f"{current_note}. {task.detail}",
                suggestion="the electronics cannot deliver more current: a bigger driver, a "
                "higher supply, or less resistance between the two. Changing the mechanics "
                "will not help while the board is at its limit",
            )
        if headroom <= LOAFING:
            return Routing(
                side="cad",
                reason="the joint missed its target while the driver had current to spare",
                evidence=f"{current_note}. {task.detail}",
                suggestion="the electronics are not the constraint: change the gear ratio, "
                "the link mass, or the joint friction. Widening a trace would change nothing",
            )
        return Routing(
            side="both",
            reason="the joint missed its target with the driver part-loaded — genuinely ambiguous",
            evidence=f"{current_note}. {task.detail}",
            suggestion=f"headroom is between {LOAFING * 100:.0f}% and {SATURATED * 100:.0f}%, so "
            "neither side is clearly at fault. Report it rather than guessing: a human should "
            "pick, or run one round of each and compare",
        )

    return Routing(
        side="both",
        reason=f"failed {', '.join(failing)} with no single owner",
        evidence=current_note,
        suggestion="review the failing checks directly",
    )


def describe(r: Routing) -> str:
    owner = {
        "pcb": "F1 — the board",
        "cad": "F2 — the mechanics",
        "both": "unattributed",
        "simulation": "neither — the simulation",
        "none": "nobody",
    }[r.side]
    lines = [f"ROUTE TO {owner}", f"  because: {r.reason}"]
    if r.evidence:
        lines.append(f"  evidence: {r.evidence}")
    if r.suggestion:
        lines.append(f"  do:       {r.suggestion}")
    return "\n".join(lines)
