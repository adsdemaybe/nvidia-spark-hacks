# Self-Critique Before Spending an Evaluation

An evaluation costs a full CAD rebuild and a physics run. Attack the proposal
first, in this order. Most bad proposals die at step 1 or 3.

1. **Does the part exist?** Check the motor against `scripts/catalogue` and the
   ratio against its listed ratios. This is the most common failure.
2. **Is a bound violated?** Out-of-range values are clamped, so the evaluation
   will not test the intended change.
3. **What currently passes that this breaks?** Name the specific criterion.
   A bigger motor adds arm mass and hurts `payload`. A shorter chassis helps
   tip-over and breaks `bay_clearance`. If you cannot name a risk, look harder.
4. **Is the physics right in direction and rough magnitude?** Shoulder torque
   scales with lever *and* the mass being levered, so a 20% length cut is more
   than a 20% torque cut.
5. **Is the rationale a mechanism or a restatement?** "Improve payload by
   improving payload" is not reasoning.

## Verdicts

- **accept** — worth an evaluation, even if imperfect.
- **revise** — right direction, wrong value or part. Say exactly what to change.
- **reject** — unbuildable, physically wrong, or it breaks more than it fixes.

Do not reject for being unambitious. A small, correct, measurable step beats a
large speculative one, because a failed large step tells you nothing about which
part of it was wrong.
