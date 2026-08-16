"""Versioned prompts for the robotics-demonstration annotation task.

Bump `PROMPT_VERSION` whenever either prompt's meaning changes — it is
recorded in every artifact's provenance.json (COSMOS_VSS.md §7) so semantic
annotations can be told apart when regenerated with a different prompt.
"""

from __future__ import annotations

PROMPT_VERSION = "robot_demo_v1"

SYSTEM_PROMPT = """You are analyzing a human manipulation demonstration for a robotics learning system.

Describe only events visually supported by the video.

Identify:
- manipulated objects
- targets or containers
- action phases
- object-object and hand-object relationships
- the final success condition

Do not invent metric 3D positions.
Do not infer hidden actions when they are not visible.
Mark uncertainty in ambiguity_notes.

Return only JSON matching the requested schema."""

_USER_PROMPT = """Analyze this manipulation demonstration.

Return:
1. task_type
2. concise natural-language instruction
3. objects and their roles
4. temporal action phases with start/end timestamps
5. important spatial relationships
6. success condition
7. ambiguity notes

Use these manipulation phases when applicable:
approach, pregrasp, grasp, lift, transport, rotate, place, release, retract.

The result will be paired later with separately recorded hand/object kinematics.
Do not generate robot joint commands.

Return a single JSON object with exactly these top-level keys:
task_type, instruction, summary, objects, timeline, spatial_relations, success_condition, ambiguity_notes.

objects: list of {"id", "label", "role", "attributes"}
  role is one of: manipulated_object, target, container, surface, obstacle, tool, other
timeline: list of {"start_s", "end_s", "phase", "description", "object_ids"}
  phase is one of: idle, approach, pregrasp, grasp, lift, transport, rotate, place, release, retract, other
spatial_relations: list of {"time_s", "subject_id", "relation", "object_id"}
  relation is one of: left_of, right_of, above, below, inside, on, near, touching, held_by_hand, other

Return only the JSON object. No prose before or after it."""


def build_user_prompt() -> str:
    return _USER_PROMPT
