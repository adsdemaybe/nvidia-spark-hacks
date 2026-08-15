# fixtures/ar-xr — the pack every AR/XR mode develops against

Two generators, deliberately separate:

| Command | Produces |
|---|---|
| `uv run python tools/make_fixtures.py` | `scene.json`, `fake_twin_state.jsonl`, `sample_follow.jsonl`, `sample_episode.{json,jsonl}`, `sample_correction.json` |
| `uv run python tools/make_assets.py` | `table.glb`, `cube.glb`, `bin.glb`, `robot.glb` |

They are split because the GLB step needs `trimesh` + `scipy`, and the JSON
step must stay runnable anywhere. Both are deterministic: no clock, no RNG,
and trimesh metadata is stripped on export, so regenerating is byte-identical
and a fixture diff always means a deliberate change.

The GLBs are crude stand-ins with correct units and plausible extents, not a
reconstruction — the real scene arrives from F3 (master plan §7). Kinematics
are **not** here: `fixtures/robot/test_arm.urdf` is the single source of truth
for the arm, loaded by both Pinocchio and MuJoCo so IK and replay cannot
silently disagree.
