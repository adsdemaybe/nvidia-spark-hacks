"""Task gates -- the pre-solved-task class of silent bug dies here."""
from __future__ import annotations

from r2s.core.gates import Severity, check, gate


def _checks(ctx) -> dict:
    return (getattr(ctx, "extra", None) or {}).get("task_checks", {})


@gate(id="TASK.EVERY_SCENE", stage="tasks", severity=Severity.HARD,
      describe="Every accepted scene got a task.",
      remediation="A scene without a task is not trainable; composer/tasks mismatch.")
def every_scene(art, ctx):
    n = len(art.payload.task_refs)
    return check("TASK.EVERY_SCENE", n >= 1,
                 measured={"tasks": n}, thresholds={"min": 1})


@gate(id="TASK.NOT_PRESATISFIED", stage="tasks", severity=Severity.HARD,
      describe="No success predicate is already TRUE on the post-settle state.",
      remediation="A pre-solved task trains a policy to do nothing; regenerate the "
                  "goal or re-place the target.")
def not_presatisfied(art, ctx):
    bad = [sid for sid, c in _checks(ctx).items() if c.get("_pre_satisfied")]
    return check("TASK.NOT_PRESATISFIED", not bad,
                 measured={"pre_satisfied": ",".join(bad) or "none"},
                 thresholds={"expect": 0},
                 msg_fail="success predicate true at t=0 -- the classic silent bug")


@gate(id="TASK.WITNESS_VALID", stage="tasks", severity=Severity.HARD,
      describe="Every task has a verified witness: a constructed goal state where "
               "the success predicate evaluates TRUE.",
      remediation="Unsatisfiable predicate (e.g. target too big for the receptacle "
                  "face) -- affordance filtering upstream should have caught it.")
def witness_valid(art, ctx):
    bad = [sid for sid, c in _checks(ctx).items() if not c.get("_witness_ok")]
    return check("TASK.WITNESS_VALID", not bad,
                 measured={"unwitnessed": ",".join(bad) or "none"},
                 thresholds={"expect": 0})


@gate(id="TASK.NON_TRIVIAL", stage="tasks", severity=Severity.HARD,
      describe="Goal is at least 5cm from the start pose.",
      remediation="A no-op task is solved by standing still.")
def non_trivial(art, ctx):
    bad = [f"{sid}:{c.get('_goal_offset_m', 0):.3f}" for sid, c in _checks(ctx).items()
           if c.get("_goal_offset_m", 0.0) < 0.05]
    return check("TASK.NON_TRIVIAL", not bad,
                 measured={"trivial": ",".join(bad) or "none"},
                 thresholds={"min_offset_m": 0.05})


@gate(id="TASK.FAMILY_SPREAD", stage="tasks", severity=Severity.SOFT,
      describe="Held-out scenes use different task families than training (axis 3).",
      remediation="Check the stratification table wiring.")
def family_spread(art, ctx):
    fams = set(art.payload.families.values())
    return check("TASK.FAMILY_SPREAD", len(fams) >= 2,
                 measured={"families": ",".join(sorted(fams))},
                 thresholds={"min_distinct": 2})
