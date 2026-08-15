# STRUCT — AR/XR Spatial Robotics Master Implementation Plan

**Scope:** Feat 4 + Feat 5  
**Primary interface:** Smartphone AR  
**Optional interface:** WebXR-compatible headset/controller  
**Compute integration:** NVIDIA DGX Spark  
**Simulation integration:** NVIDIA Isaac Sim / OpenUSD  
**Development model:** Feature-based Git branches, fixture-first development, isolated Spark workspaces  
**Core principle:** The AR/XR layer is a spatial robotics interface, not a standalone game or visualization.

---

# 0. Mission

Build the human-facing spatial layer for Struct.

The AR/XR subsystem must allow a person to:

1. **PLACE** — place and inspect a digital robot inside a real environment.
2. **TEACH** — demonstrate robot manipulation tasks spatially.
3. **REPLAY** — watch the robot reproduce a verified human demonstration.
4. **FOLLOW** — walk through a real environment while a simulated robot follows a live human target.
5. **TEST** — preview reachability, trajectories, collisions, targets, and robot behavior before deployment.
6. **CORRECT** — spatially modify an incorrect robot trajectory and capture the correction as data.
7. **TWIN** — see live simulation state spatially aligned with the real environment.
8. **IMMERSIVE** — optionally perform the same interactions through compatible XR hardware.

The system should communicate one central idea:

> **Humans communicate physical intent spatially; Struct translates that intent into robot-compatible data and brings robot intelligence back into the physical environment as a digital twin.**

---

# 1. Product Definition

The AR/XR system is the interface between:

```text
HUMAN
  ↕
PHYSICAL SPACE
  ↕
STRUCT SPATIAL LAYER
  ↕
ROBOTICS / SIMULATION
```

It operates in both directions.

## Human → Robot

```text
human moves
    ↓
spatial tracking
    ↓
normalized intent
    ↓
robot-compatible trajectory
    ↓
verification
    ↓
training / control data
```

## Robot → Human

```text
simulation state
    ↓
normalized twin state
    ↓
AR/XR client
    ↓
robot appears in physical space
```

---

# 2. Explicit Scope

This subsystem owns:

- phone spatial tracking
- XR controller tracking
- spatial coordinate normalization
- AR calibration
- virtual robot placement
- human demonstration capture
- manipulation recording
- gripper interaction
- trajectory recording
- human → robot retargeting
- demonstration verification interface
- LeRobot-compatible demonstration export
- demonstration replay
- Follow Me target generation
- human pose streaming
- live simulation-state visualization
- digital-twin alignment
- robot trajectory visualization
- target visualization
- reach visualization
- collision/debug visualization
- trajectory correction input
- optional headset/controller adapter
- shared spatial schemas and contracts
- Isaac Sim → AR live-state bridge
- OpenUSD → lightweight client visualization bridge

---

# 3. Explicit Non-Scope

The AR/XR subsystem does **not** own:

- reinforcement-learning implementation
- PPO
- RL environment design
- policy optimization
- VLA model fine-tuning
- SmolVLA training
- autonomous navigation algorithms
- obstacle-avoidance algorithms
- robot locomotion policy
- SLAM research
- physical motor commands
- robot firmware
- physical robot safety controller
- scene reconstruction
- Gaussian splatting
- COLMAP reconstruction
- semantic segmentation
- room asset extraction
- environment randomization
- PCB generation
- CAD generation
- robot manufacturing
- perception-model training

It may consume outputs from these systems.

It may produce inputs for these systems.

It must not silently absorb responsibility for them.

---

# 4. System Boundary

```text
                 EXTERNAL SYSTEMS

 F3 / ENVIRONMENT                  ROBOT TRAINING
 scene.json                        policies
 USD scene                         checkpoints
 GLB assets                        task results
 robot URDF                        trajectories
      │                                 │
      └─────────────┐   ┌───────────────┘
                    ▼   ▼

              AR/XR SYSTEM

       PLACE · TEACH · REPLAY
       FOLLOW · TEST · CORRECT
                 · TWIN

                    │
                    ▼

       normalized human demos
       LeRobot-compatible data
       human follow targets
       spatial corrections
```

---

# 5. Core Architectural Rule

No downstream robotics system may depend on:

```text
ARKitFrame
QuestControllerFrame
MediaPipeFrame
```

directly.

Every device must be converted first into a common representation:

```text
SpatialFrame
```

Therefore:

```text
PHONE ────────────┐
                  │
XR CONTROLLER ────┤
                  │
HAND TRACKING ────┼──▶ Spatial Adapter
                  │
DESKTOP MOCK ─────┤
                  │
FUTURE DEVICE ────┘
                          ↓
                    SpatialFrame
                          ↓
                SAME ROBOT PIPELINE
```

---

# 6. Repository Collaboration Rules

## 6.1 Never develop directly on `main`

All work must happen on feature-based branches.

Do not create:

```text
sky-branch
andrew-branch
```

Create branches based on capabilities.

Examples:

```text
feat/ar-contracts
feat/ar-teach
feat/ar-follow
feat/ar-twin
feat/ar-correction
feat/ar-datapipe
feat/ar-isaac-bridge
feat/ar-usd-bridge
feat/xr-web-adapter
feat/arvr-integration
```

---

# 7. Integration Branch

Use:

```text
feat/arvr-integration
```

as the temporary aggregation branch for the AR/XR subsystem.

Feature branches merge into:

```text
feat/arvr-integration
```

only after their acceptance tests pass.

The project-wide branch/main receives the AR/XR work only after the integrated subsystem passes its own gates.

---

# 8. Shared Spark Workspace Isolation

The NVIDIA Spark machine is shared infrastructure.

AR/XR development must not modify other developers' folders, worktrees, environments, or running processes unless explicitly coordinated.

At the first SSH session, identify the **existing hackathon root**.

Do not invent a second repository root.

Set:

```bash
export SPARK_HACK_ROOT="/path/to/existing/nvidia-spark-hack-root"
```

Then create:

```bash
mkdir -p "$SPARK_HACK_ROOT/ar-vr/sky"
mkdir -p "$SPARK_HACK_ROOT/ar-vr/andrew"
```

Required resulting structure:

```text
NVIDIA-SPARK-HACK-ROOT/
│
├── ...other team work...
│
└── ar-vr/
    │
    ├── sky/
    │   ├── worktrees/
    │   ├── artifacts/
    │   ├── logs/
    │   ├── fixtures/
    │   └── scratch/
    │
    └── andrew/
        ├── worktrees/
        ├── artifacts/
        ├── fixtures/
        └── scratch/
```

---

# 9. Critical Spark Ownership Rule

Only **Sky's assigned integration work** requires SSH access.

Andrew does not need to SSH into the Spark to complete his assigned features.

The directory:

```text
$SPARK_HACK_ROOT/ar-vr/andrew/
```

exists as a reserved integration/staging location.

It does **not** imply Andrew must work there.

Andrew develops through:

```text
local clone
+
feature branch
+
fixtures
+
mock backend
+
GitHub
```

Sky later integrates completed feature branches against the Spark runtime.

---

# 10. Do Not Interfere With Other Developers

Coding agents must follow these rules:

- never modify sibling team directories
- never delete unknown containers
- never kill processes that were not started by the AR/XR subsystem
- never globally upgrade system packages without coordination
- never force-push shared branches
- never reset the shared repository
- never run `git clean -fdx` at the repository root
- never change project-wide contracts without review
- never overwrite another developer's artifacts
- never modify another developer's worktree
- never use shared `/tmp` filenames without unique prefixes
- never assume an occupied port is safe to terminate

Use AR/XR-specific names for:

```text
containers
logs
ports
artifacts
temporary files
```

Example:

```text
struct-ar-api
struct-ar-isaac-bridge
struct-ar-twin
struct-ar-mock
```

---

# 11. Git Worktree Strategy on Spark

Spark-only feature branches should receive isolated worktrees.

Example:

```bash
cd "$REPO_ROOT"

mkdir -p "$SPARK_HACK_ROOT/ar-vr/sky/worktrees"
```

For the Isaac bridge:

```bash
git worktree add \
  "$SPARK_HACK_ROOT/ar-vr/sky/worktrees/isaac-bridge" \
  feat/ar-isaac-bridge
```

For USD integration:

```bash
git worktree add \
  "$SPARK_HACK_ROOT/ar-vr/sky/worktrees/usd-bridge" \
  feat/ar-usd-bridge
```

Never run multiple unrelated feature implementations from one dirty checkout.

---

# 12. Branch Ownership

| Branch | Purpose | Owner | SSH Required |
|---|---|---|---:|
| `feat/ar-contracts` | Spatial schemas/contracts | Sky | No |
| `feat/ar-teach` | Phone Teach interaction | Andrew | No |
| `feat/ar-follow` | Follow UI + target visualization | Andrew | No |
| `feat/ar-twin` | Phone twin renderer | Andrew | No |
| `feat/ar-correction` | Spatial correction UX | Andrew | No |
| `feat/xr-web-adapter` | Optional headset/browser XR | Andrew | No |
| `feat/ar-datapipe` | Normalize, retarget, export | Sky | No initially |
| `feat/ar-isaac-bridge` | Live Isaac state bridge | Sky | **Yes** |
| `feat/ar-usd-bridge` | USD validation/export integration | Sky | **Yes** |
| `feat/arvr-integration` | AR/XR integration | Sky | Only during Spark integration |

Ownership means primary implementation responsibility.

Review may be shared.

---

# 13. Sky — Responsibilities

## 13.1 Non-SSH Work

Sky owns the system contracts and robot-facing integration logic.

### A. Spatial Contracts

Implement:

```text
SpatialFrame
SpatialEpisode
TwinState
FollowState
CorrectionEvent
SceneManifest
VerificationResult
```

Freeze these early.

---

### B. Coordinate Conversion Contract

Define:

```text
device_frame
ar_world
struct_world
robot_base
end_effector
```

Canonical Struct convention:

```text
right-handed
Z-up
meters
quaternion = [x, y, z, w]
timestamps = nanoseconds
```

Device adapters must convert into this coordinate convention.

---

### C. Datapipe

Implement:

```text
raw spatial episode
        ↓
normalize
        ↓
filter
        ↓
retarget
        ↓
robot trajectory
        ↓
verification
        ↓
accepted/rejected
        ↓
dataset export
```

---

### D. Robot Retargeting

Use deterministic inverse kinematics.

Recommended:

```text
Pinocchio
```

Input:

```text
end-effector target pose
```

Output:

```text
robot joint target
```

Validate:

- joint positions
- joint limits
- velocities
- unreachable poses
- discontinuities

---

### E. Training-Data Export

Accepted episodes should be convertible into:

```text
LeRobot Dataset v3
```

The AR/XR component does not perform model training.

It guarantees compatible output.

---

### F. Integration Tests

Build tests for:

- schema validation
- unit conversions
- coordinate transformations
- transform round trips
- invalid quaternion rejection
- timestamp ordering
- follow-target calculation
- mock TwinState streaming
- recording → replay roundtrip
- rejected demonstration reporting

---

# 14. Sky — SSH / Spark-Only Responsibilities

These are the tasks that require Spark access.

Andrew must not be blocked by them.

---

## A. Spark AR/XR Workspace Setup

Create:

```text
$SPARK_HACK_ROOT/ar-vr/sky/
$SPARK_HACK_ROOT/ar-vr/andrew/
```

Then work only inside:

```text
$SPARK_HACK_ROOT/ar-vr/sky/
```

for Spark integration work.

---

## B. Isaac Sim Bridge

Isaac Sim running on Spark becomes the authoritative live simulation behind Twin mode.

NVIDIA positions Isaac Sim as its OpenUSD-based robotics simulation environment and Isaac Lab as the robot-learning layer for training, testing, and validation.

Implement:

```text
Isaac Sim
   ↓
robot joints
object poses
task state
collision/debug state
trajectory
   ↓
TwinState serializer
   ↓
WebSocket
   ↓
AR/XR clients
```

The phone does not simulate the real robot physics.

It visualizes simulation state.

---

## C. TwinState Publisher

Create a small Spark-side service:

```text
packages/isaac-bridge/
```

Responsibilities:

```text
connect to running sim
read joint state
read object transforms
read task status
read trajectory if available
normalize coordinates
serialize TwinState
publish via WebSocket
```

Suggested rate:

```text
20–60 Hz
```

Client rendering must interpolate between messages where appropriate.

---

## D. OpenUSD Integration

OpenUSD is the authoritative digital-twin representation on the simulation side.

NVIDIA's current Omniverse/OpenUSD tooling specifically supports physical-AI workflows and SimReady validation of OpenUSD assets.

Architecture:

```text
robot URDF
scene assets
environment
     ↓
OpenUSD
     ↓
Isaac Sim
```

Mobile clients should not need to render full USD.

Use:

```text
USD = simulation truth
GLB = mobile visualization representation
```

---

## E. USD Validation

When NVIDIA/OpenUSD validation tools are available on the Spark:

validate:

- scene loads
- robot loads
- asset references resolve
- scale is valid
- up-axis is correct
- transforms are finite
- required semantics exist
- simulation scene survives reload

Do not block core AR functionality on optional Omniverse agent tooling.

---

## F. Live Follow Integration

The phone sends:

```text
human_pose
+
desired_follow_target
```

Spark-side simulation consumes the target.

```text
REAL HUMAN
    ↓
phone spatial tracker
    ↓
FollowState
    ↓
Spark
    ↓
Isaac simulation target
    ↓
robot/navigation system
    ↓
TwinState
    ↓
phone
```

The AR/XR subsystem supplies the target.

It does not implement the robot's navigation algorithm.

---

## G. Live Teach Replay

After a human demo is retargeted:

```text
human demonstration
        ↓
robot trajectory
        ↓
Isaac or MuJoCo replay
        ↓
TwinState
        ↓
phone
```

This allows the human to immediately see:

> Did the robot actually reproduce what I demonstrated?

---

## H. Optional NVIDIA Demonstration Expansion Adapter

If time remains and the relevant NVIDIA workflow is available locally without adding a paid runtime dependency, expose verified manipulation demonstrations in a form that can be consumed by a GR00T-Mimic-style synthetic trajectory workflow.

NVIDIA describes GR00T-Mimic as a workflow that expands a small set of human/teleoperated demonstrations into a much larger set of synthetic robot motion trajectories.

AR/XR ownership ends at:

```text
verified human demo
      ↓
robot trajectory
      ↓
GR00T-Mimic-compatible handoff
```

AR/XR does **not** own training on the resulting synthetic dataset.

This is a stretch feature.

Do not block F4/F5 on it.

---

# 15. Andrew — Responsibilities

Andrew's development path must work completely without Spark access.

---

## A. Phone Application Structure

Build:

```text
apps/ios/
│
├── Place/
├── Teach/
├── Replay/
├── Follow/
├── Twin/
├── Correct/
├── Spatial/
├── Networking/
└── Models/
```

Primary technologies:

```text
Swift
SwiftUI
ARKit
RealityKit
WebSocket client
```

---

# 16. Andrew — PLACE Mode

Purpose:

> Preview a robot inside the real environment before deployment.

The user:

1. opens Place
2. identifies a physical floor/table location
3. places the virtual robot
4. walks around it
5. inspects reach and fit

Display:

```text
real environment
+
virtual robot
+
robot base
+
workspace
+
target markers
```

Possible information:

```text
REACHABLE ✓
OUTSIDE WORKSPACE ✗
COLLISION RISK ⚠
```

The client initially uses fixture data.

No Spark required.

---

# 17. Andrew — TEACH Mode

Purpose:

> Allow a human to spatially demonstrate robot manipulation.

The phone acts as the virtual end effector.

Map:

```text
phone position
      ↓
end-effector target position

phone orientation
      ↓
end-effector target orientation

GRAB button
      ↓
gripper closed

RELEASE button
      ↓
gripper open
```

Required controls:

```text
CALIBRATE
START DEMO
GRAB
RELEASE
FINISH
CANCEL
```

---

# 18. Teach Visual Feedback

Display:

- robot base
- virtual gripper
- trail of demonstrated path
- grab/release markers
- workspace boundary
- current frame count
- recording state
- task instruction

Example:

```text
RECORDING ●

Frames: 341
Duration: 6.2 s

─────────────→ virtual gripper

[ GRAB ]
```

---

# 19. Local Recording

Record spatial frames locally first.

Do not depend on constant network connectivity.

Pipeline:

```text
AR spatial tracker
      ↓
SpatialAdapter
      ↓
SpatialFrame
      ↓
local buffer
      ↓
episode artifact
      ↓
upload
```

If Wi-Fi disappears:

```text
demo must survive
```

---

# 20. Andrew — REPLAY Mode

Purpose:

> Show the human the robot reproduction of their demonstration.

Initial implementation uses fixture robot-state data.

Later integration swaps it for live Spark TwinState.

UI:

```text
HUMAN PATH
──────────────

ROBOT REPLAY
──────────────

tracking error
task result
```

Display result:

```text
DEMONSTRATION VERIFIED

IK                  PASS
Joint limits        PASS
Replay              PASS
Task predicate      PASS
```

or measurable failure.

---

# 21. Andrew — FOLLOW Mode

Purpose:

> Allow a person to walk through the real environment while Struct continuously creates a spatial follow target for a robot.

Phone represents the human's tracked position.

```text
PHONE
  ↓
human spatial pose
  ↓
follow-target calculation
  ↓
FollowState
```

---

# 22. Follow Target Calculation

Basic deterministic rule:

```text
follow_target =
human_position
-
human_forward × desired_follow_distance
```

Example:

```text
       movement →

      HUMAN ●

       1.5 m

      TARGET ×

      ROBOT 🤖
```

Required configurable state:

```text
follow distance
enabled
paused
stopped
```

Recommended default:

```text
1.5 m
```

---

# 23. Follow UX

Controls:

```text
START FOLLOW
PAUSE
STOP
FOLLOW DISTANCE
```

Visualize:

- human position
- desired robot position
- robot current position
- target line
- robot path
- distance error

Example:

```text
FOLLOWING

desired: 1.50 m
actual:  1.63 m
error:   0.13 m
```

---

# 24. Follow Non-Scope

The client does not decide:

```text
how robot avoids table
how robot turns wheels
how robot navigates doorway
```

The client determines:

```text
where human is
where robot should ideally be
```

and sends that target to the robotics/navigation layer.

---

# 25. Andrew — TWIN Mode

Purpose:

> Render the actual robot simulation over the physical environment.

Initial development:

```text
mock TwinState
```

Final integration:

```text
Spark Isaac TwinState
```

The renderer must not care which provider produced the state.

Define:

```text
TwinStateProvider
```

Implement:

```text
MockTwinStateProvider
WebSocketTwinStateProvider
```

---

# 26. Twin Visualization

Render:

## Real Environment

Phone camera.

## Robot

Current simulated robot.

## Ghost Robot

Optional target/future robot pose.

## Trajectory

```text
──────────────→
```

## Grasp Point

```text
×
```

## Target Object

Highlighted.

## Workspace

Robot's reachable region.

## Collision Warning

Invalid regions or collision markers.

## Follow Target

If Follow mode is active.

## Task Status

Examples:

```text
APPROACHING
GRASPING
MOVING
PLACING
SUCCESS
FAILED
```

---

# 27. Andrew — CORRECT Mode

Purpose:

> Allow humans to spatially correct robot intent without editing code.

Example:

Robot plans:

```text
A ───→ B ───→ C
          ✗ collision
```

Human drags the ghost gripper/path:

```text
A ─→ D ─→ C
       ✓
```

Generate:

```text
CorrectionEvent
```

containing:

```text
original target
corrected target
timestamp
task
reason if provided
```

This may later become correction-training data.

AR/XR owns capture.

The training system owns learning from it.

---

# 28. Andrew — Optional WebXR Adapter

Only after the phone workflows are stable.

Implement:

```text
packages/xr-web/
```

Suggested stack:

```text
TypeScript
React
Three.js
WebXR
@react-three/xr
```

A compatible tracked controller becomes another SpatialAdapter.

```text
controller pose
      ↓
SpatialFrame

trigger
      ↓
gripper
```

The headset must not create a new robot backend.

---

# 29. Shared SpatialFrame Contract

Canonical example:

```json
{
  "schema_version": "1.0",

  "timestamp_ns": 1700000000000000000,

  "source": {
    "device_type": "phone",
    "input_type": "tracked_controller"
  },

  "frame": "struct_world",

  "position_m": [
    0.31,
    0.18,
    0.42
  ],

  "orientation_xyzw": [
    0.02,
    0.71,
    0.03,
    0.70
  ],

  "gripper": 1.0
}
```

---

# 30. SpatialEpisode Contract

```json
{
  "schema_version": "1.0",

  "episode_id": "uuid",

  "task_id": "cube_to_bin",

  "source": {
    "device_type": "phone"
  },

  "coordinate_frame": "struct_world",

  "frames_artifact": "episode.parquet",

  "events": [
    {
      "type": "GRAB",
      "timestamp_ns": 1700000000000000000
    }
  ]
}
```

---

# 31. TwinState Contract

```json
{
  "schema_version": "1.0",

  "timestamp_ns": 1700000000000000000,

  "scene_id": "demo_room",

  "robot": {
    "id": "robot_01",
    "joint_positions": [
      0.1,
      0.5,
      -0.2,
      0.4,
      0.1,
      0.0
    ]
  },

  "objects": [
    {
      "id": "cube_01",

      "position_m": [
        0.3,
        0.1,
        0.7
      ],

      "orientation_xyzw": [
        0,
        0,
        0,
        1
      ]
    }
  ],

  "task": {
    "id": "cube_to_bin",
    "status": "running"
  }
}
```

---

# 32. FollowState Contract

```json
{
  "schema_version": "1.0",

  "timestamp_ns": 1700000000000000000,

  "human_pose": {
    "position_m": [
      1.0,
      2.0,
      0.0
    ],

    "orientation_xyzw": [
      0,
      0,
      0,
      1
    ]
  },

  "desired_follow_distance_m": 1.5,

  "follow_target": {
    "position_m": [
      0.0,
      2.0,
      0.0
    ]
  }
}
```

---

# 33. CorrectionEvent Contract

```json
{
  "schema_version": "1.0",

  "task_id": "cube_to_bin",

  "timestamp_ns": 1700000000000000000,

  "original_target": {
    "position_m": [
      0.4,
      0.2,
      0.5
    ]
  },

  "corrected_target": {
    "position_m": [
      0.45,
      0.25,
      0.58
    ]
  },

  "reason": "collision_avoidance"
}
```

---

# 34. Scene Manifest

AR clients should consume a lightweight manifest.

```json
{
  "schema_version": "1.0",

  "scene_id": "demo_room",

  "canonical_usd": "artifacts/demo_room/scene.usd",

  "visual_assets": [
    {
      "id": "table",
      "glb": "artifacts/table.glb"
    },

    {
      "id": "robot",
      "glb": "artifacts/robot.glb"
    }
  ]
}
```

Rule:

```text
USD = authoritative simulation representation

GLB = lightweight AR visualization representation
```

---

# 35. File Formats

## Spatial Recording

```text
metadata       → JSON
high-rate pose → Parquet
events         → JSON
```

## Robot Training

```text
final accepted data → LeRobot v3
```

## Simulation

```text
scene → OpenUSD
robot → URDF → USD representation
```

## Mobile Visualization

```text
GLB / glTF
```

## Optional iOS Packaging

```text
USDZ where useful
```

---

# 36. API Surface

## Create Episode

```http
POST /xr/episodes
```

---

## Upload Episode

```http
POST /xr/episodes/{episode_id}/artifact
```

Prefer completed Parquet artifact upload over thousands of HTTP requests.

---

## Finish Episode

```http
POST /xr/episodes/{episode_id}/finish
```

Triggers:

```text
normalize
↓
retarget
↓
verify
↓
export
```

---

## Episode Status

```http
GET /xr/episodes/{episode_id}
```

Example:

```json
{
  "status": "accepted",
  "tracking_error_m": 0.014,
  "task_success": true,
  "dataset_id": "dataset_48"
}
```

---

# 37. Scene API

```http
GET /scenes/{scene_id}
```

Returns:

```text
scene manifest
asset URLs
robot information
```

---

# 38. Twin Stream

```text
WS /twin/{scene_id}
```

Publishes:

```text
TwinState
```

---

# 39. Follow Stream

Start:

```http
POST /xr/follow
```

Then:

```text
WS /xr/follow/{session_id}
```

Phone sends:

```text
FollowState
```

Simulation returns robot state through:

```text
TwinState
```

---

# 40. Correction API

```http
POST /xr/corrections
```

Input:

```text
CorrectionEvent
```

---

# 41. PLACE Pipeline

```text
physical environment
      ↓
AR session
      ↓
user selects robot base
      ↓
load scene/robot GLB
      ↓
place virtual robot
      ↓
show workspace
      ↓
show reach / collision feedback
```

Purpose:

> Evaluate robot deployment before physical deployment.

---

# 42. TEACH Pipeline

```text
human
  ↓
phone movement
  ↓
SpatialAdapter
  ↓
SpatialFrame @ 30–60 Hz
  ↓
SpatialEpisode
  ↓
normalize coordinates
  ↓
end-effector targets
  ↓
inverse kinematics
  ↓
robot trajectory
  ↓
simulation replay
  ↓
verification
  ↓
accepted episode
  ↓
LeRobot-compatible output
```

---

# 43. REPLAY Pipeline

```text
verified robot trajectory
        ↓
simulation
        ↓
robot state
        ↓
TwinState
        ↓
AR client
        ↓
human sees robot reproduce demo
```

Replay is not autonomous policy execution.

The UI must label it clearly:

```text
REPLAYING DEMONSTRATION
```

---

# 44. FOLLOW Pipeline

```text
person walks
    ↓
phone tracks person
    ↓
human pose
    ↓
follow-target calculation
    ↓
FollowState
    ↓
robot/navigation system
    ↓
simulation
    ↓
TwinState
    ↓
AR robot visibly follows
```

---

# 45. TEST Pipeline

Test mode combines:

```text
PLACE
+
FOLLOW
+
LIVE TWIN
```

Potential questions answered:

```text
Can this robot fit here?

Can this robot reach that shelf?

Can the robot follow me around this table?

Does its planned path collide?

Where will it stand?

Can it maintain the requested distance?
```

This turns the digital twin into a deployment simulator.

---

# 46. CORRECT Pipeline

```text
robot proposes trajectory
        ↓
AR displays trajectory
        ↓
human sees problem
        ↓
human adjusts ghost target/path
        ↓
CorrectionEvent
        ↓
verification
        ↓
stored correction
```

Long-term:

```text
robot action
+
human corrected action
=
high-value supervision
```

---

# 47. TWIN Pipeline

```text
F3 reconstructed scene
        ↓
OpenUSD simulation scene
        ↓
Isaac Sim on Spark
        ↓
live robot/object state
        ↓
TwinState
        ↓
WebSocket
        ↓
phone / optional XR headset
        ↓
spatial overlay
```

This is the core Feat 5 requirement.

---

# 48. What Makes This a Digital Twin

Do not claim digital twinning if the app only renders a static 3D robot.

Feat 5 is considered a genuine twin only when:

```text
physical environment
        ↕
spatial alignment
        ↕
digital environment
        ↕
live simulation state
```

Minimum requirements:

1. scene corresponds to the reconstructed environment
2. scene is spatially aligned to physical references
3. robot state originates from the simulation
4. incoming simulation state updates the AR robot
5. object/task state can update live

---

# 49. Twin Alignment v0

Use deterministic manual anchors first.

Example:

```text
Anchor A = robot base

Anchor B = table corner
```

Compute:

```text
T_struct_to_ar
```

Then transform all incoming twin poses into the AR coordinate frame.

Target:

```text
anchor reprojection error < 5 cm
```

Do not delay the demo for automatic relocalization.

---

# 50. Twin Alignment Stretch

Optional:

- plane detection
- environment mesh
- additional anchors
- automatic floor alignment
- LiDAR-assisted registration
- persistent anchors

These are enhancements.

They are not prerequisites.

---

# 51. NVIDIA Role

The NVIDIA integration must provide genuine functionality.

---

## NVIDIA DGX Spark

Spark runs the heavy simulation/integration workloads.

It is not required for:

```text
phone UI
fixtures
schemas
follow math
mock twin
WebXR frontend
```

It is required for the final integration with the project's NVIDIA simulation stack.

---

# 52. NVIDIA Isaac Sim

Isaac Sim is the **live digital-twin simulation source**.

NVIDIA currently positions Isaac Sim for robotics simulation, synthetic data, testing, and validation, with Isaac Lab supporting robot learning workflows.

AR consumes its state.

AR does not replace it.

---

# 53. NVIDIA OpenUSD / Omniverse

The authoritative twin remains OpenUSD on the simulation side.

NVIDIA's current Omniverse/OpenUSD stack includes libraries and SimReady workflows intended for physical-AI and simulation-ready asset preparation/validation.

Use Omniverse tooling where available to:

- validate USD
- inspect digital twin
- verify asset readiness
- prepare simulation-ready assets
- debug scene structure

Do not make an optional agent skill a hard dependency.

---

# 54. Optional NVIDIA GR00T-Mimic Handoff

Human demonstrations are one of the most valuable outputs of the AR subsystem.

Optional extension:

```text
AR human demo
      ↓
verified robot trajectory
      ↓
GR00T-Mimic-compatible handoff
      ↓
synthetic trajectory expansion
      ↓
training system
```

NVIDIA documents GR00T-Mimic as a synthetic manipulation workflow for expanding a small number of human demonstrations into many synthetic robot motion trajectories.

Rules:

- optional
- no paid external runtime required for core AR/XR
- only use if accessible in the hackathon environment
- do not block demo on it
- training remains outside AR/XR ownership

---

# 55. What Not to Pull Into AR/XR

Do not absorb:

```text
NuRec reconstruction
Gaussian-splat training
scene segmentation
Isaac RL
policy training
```

Those belong elsewhere.

AR/XR only consumes their artifacts/states where necessary.

---

# 56. Free / Local Runtime Stack

## Mobile

```text
Swift
SwiftUI
ARKit
RealityKit
```

## Shared Backend

```text
Python 3.11
FastAPI
Pydantic
NumPy
PyArrow
```

## Robot Geometry / Verification

```text
Pinocchio
MuJoCo
LeRobot
```

## NVIDIA Integration

```text
DGX Spark
Isaac Sim
OpenUSD
Omniverse validation tools where available
```

## Optional Browser XR

```text
TypeScript
React
Three.js
WebXR
@react-three/xr
```

## Development Agents

```text
Codex
Claude Code
```

Codex and Claude Code are development tools.

They must not become production runtime dependencies.

---

# 57. Required Fixture Pack

Nobody should wait for another feat before developing.

Commit:

```text
fixtures/ar-xr/
│
├── scene.json
├── table.glb
├── cube.glb
├── bin.glb
├── robot.glb
├── fake_twin_state.jsonl
├── sample_episode.parquet
├── sample_follow.jsonl
└── sample_correction.json
```

---

# 58. Mock Twin Server

Create:

```text
tools/mock_twin_server.py
```

It should stream:

```text
fake TwinState
```

at configurable frequency.

Example:

```bash
python tools/mock_twin_server.py --hz 30
```

Andrew develops against this.

Later:

```text
MockTwinStateProvider
```

becomes:

```text
IsaacTwinStateProvider
```

without client rewrites.

---

# 59. Development Sequence

## Phase 0 — Contract Freeze

Build:

```text
SpatialFrame
SpatialEpisode
TwinState
FollowState
CorrectionEvent
SceneManifest
```

No feature proceeds with private incompatible schemas.

---

## Phase 1 — Fixtures

Create fake scene + fake robot + fake stream.

---

## Phase 2 — Place

Phone displays robot spatially.

---

## Phase 3 — Teach

Phone records motion and gripper state.

---

## Phase 4 — Record

Persist one full spatial episode.

---

## Phase 5 — Retarget

Convert spatial trajectory to robot joint trajectory.

---

## Phase 6 — Verify

Replay and return deterministic success/failure.

---

## Phase 7 — Replay

Phone watches robot reproduce the demonstration.

---

## Phase 8 — Follow

Phone produces continuously updated FollowState.

---

## Phase 9 — Twin With Mock Data

Phone renders fake live robot state.

---

## Phase 10 — Correct

Human edits ghost trajectory and saves correction.

---

## Phase 11 — Spark Integration

Sky connects:

```text
real Isaac state
↓
TwinState
↓
existing client
```

---

## Phase 12 — Real Scene

Replace fixtures with actual `scene.json`/GLB/USD artifacts.

---

## Phase 13 — Optional Headset

Implement WebXR adapter.

---

## Phase 14 — Optional GR00T-Mimic Handoff

Only after the primary loop is stable.

---

# 60. Acceptance Gate — Contracts

Pass only if:

```text
all schemas versioned
units documented
coordinate frames documented
example payloads validate
phone and backend share same definitions
unknown schema versions rejected
```

---

# 61. Acceptance Gate — Teach

Pass only if:

```text
spatial pose complete
timestamp monotonic
coordinate conversion valid
recording survives network loss
gripper events persist
episode uploads successfully
```

---

# 62. Acceptance Gate — Retarget

Pass only if:

```text
IK succeeds
joint limits valid
velocity limits valid
trajectory finite
no invalid quaternion
no NaN
no unexplained discontinuity
```

---

# 63. Acceptance Gate — Verification

A demonstration becomes usable robot data only if:

```text
retarget succeeds
AND
replay succeeds
AND
tracking error acceptable
AND
task predicate passes
```

Rejected demos must return a measurable reason.

---

# 64. Acceptance Gate — Follow

Pass only if:

```text
human pose valid
follow distance valid
target finite
coordinate transform valid
target updates continuously
STOP immediately halts target generation
```

Navigation may reject the target separately.

That is not an AR failure.

---

# 65. Acceptance Gate — Twin

Pass only if:

```text
scene loads
robot loads
anchors resolve
TwinState validates
joint state visibly updates robot
object state visibly updates object
state disconnect handled safely
```

Final integration target:

```text
live TwinState from Isaac
```

not mock state.

---

# 66. Acceptance Gate — Digital Twin Claim

Do not call the feature a completed digital twin until:

```text
actual reconstructed scene
+
real physical alignment
+
live Isaac simulation state
```

are connected.

A static robot placed through AR is:

```text
AR visualization
```

not a complete digital twin.

---

# 67. Definition of Done — Feat 4

Feat 4 is complete when:

1. phone can calibrate spatial frame
2. user can record manipulation demonstration
3. demonstration uses canonical SpatialFrame
4. gripper events are recorded
5. demonstration is retargeted to robot
6. verification produces PASS/FAIL
7. valid demonstration can replay
8. accepted demonstration exports to LeRobot-compatible dataset
9. optional XR controller can eventually emit the same SpatialFrame

---

# 68. Definition of Done — Feat 5

Feat 5 is complete when:

1. actual scene manifest loads
2. robot visual asset loads
3. physical anchors align the digital scene
4. Isaac state is streamed from Spark
5. AR robot follows live simulation state
6. target/path/task information can be visualized
7. user can move spatially around the twin
8. Follow mode can use the same live twin
9. client survives temporary disconnect/reconnect

---

# 69. Definition of Done — Follow

Follow is complete when:

1. user starts Follow
2. phone produces human pose
3. follow target updates continuously
4. target is visible
5. target is sent to robot/simulation interface
6. returned robot state is visible
7. user can pause
8. user can stop immediately

---

# 70. Definition of Done — Correct

Correct is complete when:

1. robot trajectory is visible
2. human can modify a target/path point spatially
3. original and corrected targets are preserved
4. CorrectionEvent is emitted
5. correction can be replayed or verified

---

# 71. Primary Demo

## Scene

Simple environment:

```text
table
cube
bin
robot
```

Do not overcomplicate the demo scene.

---

# 72. Demo — PLACE

Point phone at physical workspace.

Tap:

```text
PLACE ROBOT
```

Virtual robot appears spatially aligned.

Show:

```text
workspace
reach
target
```

Explain:

> The robot can be evaluated inside the deployment environment before physical deployment.

---

# 73. Demo — FOLLOW

Select:

```text
FOLLOW ME
```

Walk around the table.

Laptop/phone displays:

```text
human ●

desired target ×

robot 🤖

planned route ─────→
```

Robot simulation responds to live human movement.

---

# 74. Demo — TEACH

Select:

```text
TEACH
```

Task:

```text
Move cube into bin.
```

Move phone:

```text
approach
↓
GRAB
↓
lift
↓
move
↓
RELEASE
```

---

# 75. Demo — VERIFY

Immediately display:

```text
DEMONSTRATION CAPTURED

Frames              418 ✓
Coordinate transform ✓
IK                   ✓
Joint limits         ✓
Replay               ✓
Task predicate       ✓

DEMONSTRATION ACCEPTED
```

---

# 76. Demo — REPLAY

Select:

```text
REPLAY
```

The robot reproduces the human's demonstrated action.

State clearly:

```text
REPLAYING VERIFIED HUMAN DEMONSTRATION
```

---

# 77. Demo — CORRECT

Show a deliberately imperfect path.

Example:

```text
robot path intersects obstacle
```

Human adjusts ghost target.

Display:

```text
CORRECTION CAPTURED
```

Replay corrected path.

---

# 78. Demo — LIVE TWIN

Switch to:

```text
TWIN
```

Point phone at physical workspace.

Show:

```text
REAL ROOM
+
VIRTUAL ROBOT
+
LIVE ISAAC STATE
+
TRAJECTORY
+
TARGET
+
TASK STATUS
```

This is the Feat 5 closing shot.

---

# 79. Optional Headset Demo

Only use if stable.

A headset/controller should connect to the same backend.

Demonstrate:

```text
controller moves
↓
same SpatialFrame
↓
same robot
```

The headset proves hardware optionality.

It is not the main demo dependency.

---

# 80. Demo Recording / Projection

Optional headset view may be mirrored/cast to a laptop.

The presentation should ideally show:

```text
┌────────────────────────────────────────┐
│              STRUCT LIVE               │
├────────────────────┬───────────────────┤
│ HUMAN / AR VIEW    │ ISAAC SIM VIEW    │
│                    │                   │
│ what user sees     │ what robot does   │
├────────────────────┴───────────────────┤
│ SpatialFrame → Robot → TwinState       │
└────────────────────────────────────────┘
```

The dual view makes the physical/digital loop understandable.

---

# 81. UI Vocabulary

Use consistent product language:

```text
PLACE
TEACH
REPLAY
FOLLOW
TEST
CORRECT
TWIN
```

Avoid vague labels such as:

```text
AR Mode
XR Thing
Simulation View
```

---

# 82. Fail Gracefully

If Isaac is unavailable:

```text
Twin → Mock mode
```

Clearly label:

```text
SIMULATION DISCONNECTED
USING FIXTURE STREAM
```

Do not present fixture state as live Isaac state.

---

# 83. If Training Is Not Ready

Replay remains valid.

Distinguish:

```text
REPLAY DEMO
```

from:

```text
RUN POLICY
```

Never imply scripted or recorded motion is learned autonomous behavior.

---

# 84. If Actual Room Assets Are Not Ready

Use fixtures while developing.

Final Feat 5 integration must attempt:

```text
actual scene.json
+
actual GLB assets
+
actual USD scene
```

before claiming full digital twinning.

---

# 85. Coding Agent Rules

Any coding agent implementing this spec must:

1. read contracts before implementation
2. identify its assigned feature branch
3. avoid unrelated directories
4. create tests with each feature
5. use fixtures before waiting for integrations
6. preserve coordinate conventions
7. never silently convert units
8. never invent missing robot state
9. never mark verification success from an LLM judgment
10. preserve rejected episode reasons
11. avoid paid runtime dependencies
12. prefer local/open tooling
13. never force-push shared branches
14. never modify another developer's Spark directory
15. never change frozen contracts without review

---

# 86. Coding Agent Start Command — Non-SSH Features

For Andrew or any non-Spark feature:

```bash
git fetch origin
git switch -c feat/<feature-name>
```

Examples:

```bash
git switch -c feat/ar-teach
```

or:

```bash
git switch -c feat/ar-follow
```

Work locally.

Test against fixtures.

Push:

```bash
git push -u origin feat/ar-teach
```

Open/prepare for integration into:

```text
feat/arvr-integration
```

---

# 87. Coding Agent Start Procedure — Spark Features

Only Sky's Spark integration agent should perform this procedure.

1. SSH into the Spark.
2. Locate the existing repository/hack root.
3. Confirm:

```bash
git status
```

4. Do not modify the shared checkout.
5. Set:

```bash
export SPARK_HACK_ROOT="/existing/hack/root"
```

6. Ensure:

```bash
mkdir -p "$SPARK_HACK_ROOT/ar-vr/sky/worktrees"
mkdir -p "$SPARK_HACK_ROOT/ar-vr/sky/artifacts"
mkdir -p "$SPARK_HACK_ROOT/ar-vr/sky/logs"

mkdir -p "$SPARK_HACK_ROOT/ar-vr/andrew/worktrees"
mkdir -p "$SPARK_HACK_ROOT/ar-vr/andrew/artifacts"
mkdir -p "$SPARK_HACK_ROOT/ar-vr/andrew/fixtures"
```

7. Create isolated feature worktree.
8. Run only AR/XR-specific services.
9. Write logs under:

```text
$SPARK_HACK_ROOT/ar-vr/sky/logs/
```

10. Write generated artifacts under:

```text
$SPARK_HACK_ROOT/ar-vr/sky/artifacts/
```

---

# 88. Final Architecture

```text
                         HUMAN
                           │
             ┌─────────────┴─────────────┐
             │                           │
          PHONE AR                  OPTIONAL XR
             │                           │
             └─────────┬─────────────────┘
                       │
                SpatialAdapter
                       │
                       ▼
                 SpatialFrame
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
      PLACE          TEACH          FOLLOW
                       │              │
                       ▼              ▼
                 SpatialEpisode   FollowState
                       │              │
                       ▼              │
                  Retarget            │
                       │              │
                       ▼              │
                 Verification         │
                       │              │
                       ▼              │
                 LeRobot Data         │
                       │              │
                       └──────┬───────┘
                              │
                              ▼
                    ROBOTICS / TRAINING
                              │
                              ▼
                     NVIDIA DGX SPARK
                              │
                              ▼
                        ISAAC SIM
                              │
                    OpenUSD Digital Twin
                              │
                              ▼
                         TwinState
                              │
                              ▼
                 ┌────────────┴───────────┐
                 │                        │
               PHONE                  OPTIONAL XR
                 │
                 ▼
          PLACE / REPLAY /
        FOLLOW / CORRECT / TWIN
```

---

# 89. Final Product Statement

The Struct AR/XR subsystem is not a VR game and is not merely a robot visualizer.

It is a **spatial operating interface for physical AI**.

It lets humans:

```text
PLACE a robot before deployment

TEACH behavior through natural motion

REPLAY verified demonstrations

FOLLOW humans through a real environment

TEST robot behavior before physical execution

CORRECT robot intent spatially

TWIN live simulation back into the physical world
```

Its core architectural promise is:

```text
ANY SPATIAL INPUT
        ↓
NORMALIZED HUMAN INTENT
        ↓
ROBOT-COMPATIBLE DATA
        ↓
SIMULATION / LEARNING
        ↓
LIVE ROBOT STATE
        ↓
ANY SPATIAL VIEWER
```

The smartphone is the primary demo device.

Headsets are optional.

NVIDIA DGX Spark, Isaac Sim, OpenUSD, and Omniverse tooling power the final simulation-backed digital twin rather than acting as decorative integrations. NVIDIA's current platform explicitly centers Isaac Sim around robotics simulation/validation and OpenUSD/Omniverse around physical-AI digital-twin and simulation-ready workflows.

The subsystem remains independently developable through stable contracts, fixtures, mock state providers, and feature-based branches until final Spark integration.