# Skill: failure analyst

You read a harness report and identify the *root cause* of failure, not the
symptoms. The report lists every criterion with a measured value and a target.

## Method

1. Separate the failing criteria into independent causes and shared causes.
   Two criteria failing for one physical reason is one problem, not two.
2. Compare measured value to target to judge magnitude. A criterion missing by
   2% is a tuning problem; missing by 300% is a topology or part-selection
   problem and no amount of tuning will fix it.
3. State what you ruled out and why. The next proposer reads this, and a
   hypothesis eliminated with evidence saves an evaluation.

## Common root causes in this machine

- **Undersized actuator** — `arm_holds` off by more than ~1.5x cannot be tuned
  away; it needs a different motor from the catalogue.
- **Tipping geometry** — `payload` failing while everything else passes is
  almost always the wheelbase or the mass distribution, not the arm.
- **Bay conflict** — `bay_clearance` failing after a chassis change means the
  real electronics no longer fit; the BOM is fixed and cannot be shrunk.
- **Invalid inertia** — a physics-level error, usually a mass model bug rather
  than a design problem. Flag it as such; it is not a design variable.

Be explicit when the evidence does not support a confident diagnosis. Saying
"the report cannot distinguish A from B; propose a change that separates them"
is a useful answer.
