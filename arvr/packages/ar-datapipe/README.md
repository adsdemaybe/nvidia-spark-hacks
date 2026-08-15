# ar-datapipe — Sky — `feat/ar-datapipe`

normalize → retarget (Pinocchio IK) → verify (MuJoCo replay) → export
(LeRobot-compatible). Entry point: `ar_datapipe.run_episode(episode, frames,
goal_position_m=..., dataset_root=...)` → `VerificationResult`.

Targets the placeholder test arm in `../../fixtures/robot/test_arm.urdf`
(NOT the real deployment robot — see that directory's README). One URDF
drives both Pinocchio (IK) and MuJoCo (replay/verification), so the two
engines can never silently disagree about the kinematic chain.

## Why MuJoCo instead of Isaac Sim here

Isaac Sim is the spec's stated authority for Twin-mode simulation (section
14B), but it is not installed on the Spark yet, and even once it is, this
package should stay independent of a running Isaac instance — this is
per-frame kinematic verification (does the IK solution + joint-limit check
+ an independent FK engine agree the tip lands where commanded?), not
scene/physics simulation. MuJoCo is free, starts instantly, and needs no
GPU, so `feat/ar-datapipe` tests run in CI/WSL without any Spark dependency
at all — matches spec section 56's "free/local runtime stack" and section
53's "don't make an optional agent skill a hard dependency."

## Platform note (read before debugging a Windows `uv sync` failure)

Pinocchio's Windows wheel coverage (via the `cmeel` packaging project) is
incomplete for some transitive deps as of `pin` 3.4-4.1 (`cmeel-octomap`,
`cmeel-boost` in some combinations need a full MSVC+CMake build that this
repo doesn't assume). `pin`/`mujoco`/`pyarrow` are therefore gated to
`sys_platform == 'linux'` in this package's `pyproject.toml` — `uv sync`
still succeeds on Windows for the rest of the workspace, but
`import ar_datapipe` will fail there once you touch retarget/verify/export.
`tests/test_datapipe.py` handles this with `pytest.importorskip`.

Run this package's tests on Linux: the Spark itself, or locally via WSL:

```bash
wsl -d Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
cd /mnt/c/.../nvidia-spark-hacks/arvr
# libgomp1 (OpenMP runtime) is a Pinocchio import-time dependency; if you
# don't have sudo, `apt-get download libgomp1 && dpkg-deb -x ... ~/.local/libgomp`
# and put it on LD_LIBRARY_PATH works without root.
uv sync && uv run pytest tests/ -q
```

## Known judgment calls (flag for review)

- **IK is a damped CLIK loop** (`retarget.py`), not a closed-form solve —
  standard for a 6-DOF non-spherical-wrist arm. Step size is clamped
  (`MAX_STEP_NORM`) after an early version spiraled a revolute joint
  through multiple full turns before "converging" to a kinematically valid
  but nonsensical 48-radian solution; the fix also wraps the final answer
  into `(-pi, pi]` since revolute FK is periodic.
- **`verify.py` is kinematic only** (`mj_forward`, no dynamics stepping) —
  it's independently cross-checking Pinocchio's FK, not simulating contact
  physics. A real dynamic replay (PD control tracking a trajectory) is a
  reasonable Phase 11+ enhancement once there's a real robot to tune gains
  for.
- **LeRobot export is best-effort**, not validated against the actual
  `lerobot` package (see `export.py` docstring) — that package pulls in
  torch/gymnasium, much heavier than this export step needs.
