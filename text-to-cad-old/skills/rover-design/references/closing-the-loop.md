# Closing the Loop: Critic Findings Become Code Changes

The critic's two finding types demand two *different* kinds of fix, and only one
of them is a design-variable change. This is the part of the loop that cannot be
a deterministic script, because writing a new criterion requires deciding what
"correct" means — which is judgement, not search.

| Finding | What it means | Correct fix | Who does it |
| --- | --- | --- | --- |
| `FRAGILE` | A criterion flips under small perturbation — no margin | Usually a **design variable** change; sometimes new geometry | search, or the agent |
| `BLIND` | No criterion responds to this variable | A **new criterion**, i.e. new code | the agent, always |

A search loop can never fix a `BLIND` finding. It only moves values inside a
space that the criteria define; if a subsystem is unmeasured, every value is
equally good and the optimiser has no gradient. The harness itself must change.

## The rule

**A `BLIND` finding is a work item against the harness, not the design.**

Do not tune around it. Do not report the design as converged while it stands.
Write the criterion, re-run, and only then continue optimising.

## Worked example: `mount_fits`

The loop converged three times on a design whose shoulder motor could not be
bolted on. The NEMA23's bolt circle overhung the 34 mm yoke prong by 6.6 mm.
Every criterion passed, because the CAD kernel treats "cut a hole outside the
part" as a no-op: no holes were made, and `cad_builds` saw a valid solid.

The fix was not a better optimiser. It was thirty lines of new code:

```python
def check_mount_fit() -> list[str]:
    """Every motor mount's fastener pattern must land on actual material."""
    m = MOTORS[SHOULDER_MOTOR]
    need = max(m["bolt_pitch"] / 2 + m["hole_d"] / 2, m["pilot_d"] / 2)
    if need > LINK_W / 2:
        return [f"{SHOULDER_MOTOR} overhangs by {need - LINK_W / 2:.1f} mm"]
    return []
```

Then a **new design variable** had to be exposed — `LINK_W` — because with the
criterion in place and the yoke width fixed, no NEMA23 could ever be mounted.
Adding a criterion frequently forces adding a variable; otherwise the criterion
is unsatisfiable and the loop stalls instead of converging.

That criterion then transferred unchanged to the quadruped and immediately found
the same class of defect on the hip and knee mounts. Criteria are the reusable
asset here; the design variables are not.

## Procedure

1. Run `scripts/critique`. Read the `BLIND` list.
2. For each blind variable, ask: *what would going wrong look like?* Gripper
   aperture too small to grasp anything. Track too narrow to resist a lateral
   push. Wall too thin to print. Name the failure before writing the check.
3. Write the criterion as a `check_*()` returning a list of human-readable
   failure strings, and add it to `evaluate()`. Follow the existing shape.
4. Re-run `critique`. Confirm the variable now shows a response **above the
   coverage threshold**. A criterion that moves by 0.1% has not fixed anything —
   that is the incidental mass coupling that made the first blind detector give
   a false negative.
5. If the new criterion is unsatisfiable at every value, expose the design
   variable that would satisfy it, with honest bounds.
6. Re-run `refine`. The design that "converged" before will usually now fail,
   which is the criterion doing its job.

## What this looks like when it is working

Each pass through the loop should make the harness harder to satisfy, not
easier. A converged design under a weak harness is worth less than a failing
design under a strong one, because only the second tells you something true.

Measure progress by criteria coverage — how many design variables have a real
responder — not by how quickly the score reaches zero.
