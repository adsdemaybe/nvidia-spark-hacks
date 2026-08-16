# COSMOS_VSS.md — Standalone Semantic Video Understanding for Robotics Demonstrations

## 0. Mission

Build **Cosmos/VSS as a standalone feature** for the robotics hackathon.

This component must **not edit, rewrite, or own**:

- CAD generation
- PCB generation
- robot embodiment generation
- the existing AR/VR hand-demonstration collector
- the existing `HumanEpisode` format
- the existing LeRobot export
- the RL trainer
- Isaac Lab reward functions
- robot retargeting
- robot motor/control code

The existing AR/VR data collector remains responsible for **how the human moved**:
hand joints, wrist pose, grasp state, object pose/velocity, timestamps, and events.

This new component is responsible for **what happened in the video**:
task description, objects involved, temporal phases, spatial relationships, and a concise semantic representation of the demonstration.

The two outputs may share an `episode_id`, but neither subsystem should depend on the internal implementation of the other.

---

# 1. Product idea

For a recorded or live human manipulation demonstration:

> "Pick up the red bottle, rotate it upright, move it to the right basket, and release it."

the existing AR/VR collector produces motion/object data.

Cosmos/VSS independently watches the video and produces semantic metadata such as:

```json
{
  "task": "pick_and-place",
  "instruction": "Move the red bottle into the right basket.",
  "objects": [
    {"id": "bottle_1", "label": "red bottle", "role": "manipulated_object"},
    {"id": "basket_1", "label": "basket", "role": "target"}
  ],
  "timeline": [
    {"start_s": 0.0, "end_s": 1.2, "phase": "approach"},
    {"start_s": 1.2, "end_s": 2.0, "phase": "grasp"},
    {"start_s": 2.0, "end_s": 3.1, "phase": "lift"},
    {"start_s": 3.1, "end_s": 5.8, "phase": "transport"},
    {"start_s": 5.8, "end_s": 6.4, "phase": "release"}
  ],
  "success_condition": "red bottle is inside the target basket"
}
```

This metadata is written as a **new sidecar artifact**.

Do not inject it into the LeRobot dataset in this implementation.

---

# 2. Final hackathon architecture

```text
                    HUMAN DEMONSTRATION
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
      EXISTING AR/VR CAPTURE      NEW COSMOS/VSS
      do not modify               standalone service
              │                         │
              │                         │
   hand joints / wrist             RGB video
   object pose / velocity              │
   grasp + task events                 ▼
              │                    VSS ingestion
              │                         │
              │                         ▼
              │                  Cosmos Reason
              │                         │
              ▼                         ▼
      HumanEpisode / LeRobot     SemanticEpisode JSON
              │                         │
              └────────────┬────────────┘
                           │
                    shared episode_id
                           │
                           ▼
                    FUTURE FUSION LAYER
                    not part of this task
                           │
                           ▼
                     RL / policy work
```

The Cosmos/VSS service must still work when the RL service is offline.

The data collector must still work when Cosmos/VSS is offline.

---

# 3. Why use VSS here

Use NVIDIA VSS as the **video ingestion and video-intelligence layer**.

Use NVIDIA Cosmos Reason as the **physical-world reasoning VLM**.

For this hackathon, do not build a generic VSS chat application or RAG/search product.

We only need:

1. recorded-video ingestion
2. optional live-video ingestion
3. Cosmos-backed video reasoning
4. structured robotics-task output
5. a tiny API/CLI
6. saved semantic artifacts
7. a simple debug/demo panel

Prefer the direct VSS REST surface over adding a second general-purpose agent orchestration layer.

---

# 4. Repository boundary

Create a new package:

```text
packages/
  cosmos-vss/
    README.md
    pyproject.toml

    src/
      cosmos_vss/
        __init__.py
        app.py
        cli.py
        config.py
        schemas.py
        prompts.py
        vss_client.py
        cosmos_client.py
        analyzer.py
        parser.py
        artifacts.py
        rtsp.py

    tests/
      test_schemas.py
      test_parser.py
      test_analyzer_mock.py
      test_artifacts.py
      test_api.py

    fixtures/
      simple_pick_place.expected.json

tools/
  cosmos_vss_doctor.py
  webcam_to_rtsp.md

artifacts/
  semantic/
    .gitkeep
```

Do **not** move existing AR/VR code into this package.

Do **not** import application internals from:

```text
packages/xr-web/
packages/ar-datapipe/
```

The only allowed cross-feature concept is an optional string:

```text
episode_id
```

No code-level dependency on `HumanEpisode` is required.

---

# 5. Runtime modes

Implement two backends behind one interface.

## Backend A — VSS Real-Time VLM

Primary path.

```text
video
  ↓
VSS Real-Time VLM REST API
  ↓
Cosmos Reason
  ↓
structured semantic result
```

Environment:

```bash
COSMOS_VSS_BACKEND=vss
VSS_BASE_URL=http://<spark-host>:8000/v1
VSS_MODEL=cosmos-reason2
```

The application must check:

```http
GET /v1/health/ready
GET /v1/models
```

before analysis.

For recorded files:

```text
POST /v1/files
      ↓
file_id
      ↓
POST /v1/generate_captions
```

or use the VSS chat-completions media input if that is simpler for the installed version.

For the task-specific pass, use a robotics-specific prompt rather than a generic caption prompt.

## Backend B — direct Cosmos NIM

Fallback/debug path.

```text
video
  ↓
Cosmos Reason NIM
  ↓
POST /v1/chat/completions
  ↓
structured semantic result
```

Environment:

```bash
COSMOS_VSS_BACKEND=cosmos
COSMOS_BASE_URL=http://<spark-host>:8000/v1
COSMOS_MODEL=nvidia/cosmos-reason2-8b
```

This exists so the feature is testable even if the complete VSS profile is unavailable.

Do not silently switch backends.

If VSS fails, surface:

```json
{
  "status": "error",
  "backend": "vss",
  "reason": "..."
}
```

A fallback is only used when explicitly configured.

---

# 6. Data contract

Create `schemas.py` with Pydantic models.

## SemanticObject

```python
class SemanticObject(BaseModel):
    id: str
    label: str
    role: Literal[
        "manipulated_object",
        "target",
        "container",
        "surface",
        "obstacle",
        "tool",
        "other",
    ]
    attributes: dict[str, str] = {}
```

Do not fabricate metric XYZ coordinates from a monocular semantic VLM response.

If a model returns 2D image coordinates, store them separately and label the coordinate system explicitly.

## TemporalPhase

```python
class TemporalPhase(BaseModel):
    start_s: float
    end_s: float
    phase: Literal[
        "idle",
        "approach",
        "pregrasp",
        "grasp",
        "lift",
        "transport",
        "rotate",
        "place",
        "release",
        "retract",
        "other",
    ]
    description: str
    object_ids: list[str] = []
```

## SpatialRelation

```python
class SpatialRelation(BaseModel):
    time_s: float | None = None
    subject_id: str
    relation: Literal[
        "left_of",
        "right_of",
        "above",
        "below",
        "inside",
        "on",
        "near",
        "touching",
        "held_by_hand",
        "other",
    ]
    object_id: str
```

## SemanticEpisode

```python
class SemanticEpisode(BaseModel):
    schema_version: str = "1.0"
    semantic_id: str
    episode_id: str | None
    source_name: str
    source_type: Literal["file", "url", "rtsp"]
    backend: Literal["vss", "cosmos"]
    model: str

    task_type: str
    instruction: str
    summary: str

    objects: list[SemanticObject]
    timeline: list[TemporalPhase]
    spatial_relations: list[SpatialRelation]

    success_condition: str | None
    ambiguity_notes: list[str]

    video_duration_s: float | None
    created_at: str
```

Keep the original raw model result in a separate file for debugging:

```text
artifacts/semantic/<semantic_id>/
  semantic.json
  raw_response.json
  request.json
  provenance.json
```

Do not place raw chain-of-thought/reasoning text into the public schema.

---

# 7. Provenance

Every semantic artifact must record:

```json
{
  "backend": "vss",
  "model": "cosmos-reason2",
  "source_sha256": "...",
  "source_name": "demo_001.mp4",
  "episode_id": "episode_001",
  "prompt_version": "robot_demo_v1",
  "schema_version": "1.0"
}
```

Hash local input files before submission.

This is important because semantic annotations may be regenerated later with a different model or prompt.

---

# 8. Prompting strategy

Create versioned prompts in `prompts.py`.

Do not ask:

> "Describe this video."

Ask Cosmos to behave specifically as a robotics demonstration annotator.

## System prompt

```text
You are analyzing a human manipulation demonstration for a robotics learning system.

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

Return only JSON matching the requested schema.
```

## User prompt

```text
Analyze this manipulation demonstration.

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
```

---

# 9. Parsing

Never trust model text directly.

`parser.py` must:

1. strip markdown fences if present
2. locate a JSON object
3. parse JSON
4. validate against `SemanticEpisode`
5. reject invalid timestamps
6. enforce `end_s >= start_s`
7. enforce sorted timeline
8. ensure referenced object IDs exist
9. return a structured parse error instead of crashing

Do not "repair" unsupported semantic claims with hardcoded guesses.

A second model call for JSON repair is optional and must be explicit.

---

# 10. Analyzer interface

Create one internal interface:

```python
class VideoSemanticAnalyzer(Protocol):
    def analyze(
        self,
        source: VideoSource,
        *,
        episode_id: str | None = None,
    ) -> SemanticEpisode:
        ...
```

Backends:

```text
VssAnalyzer
CosmosNimAnalyzer
MockAnalyzer
```

`MockAnalyzer` is mandatory for CI.

Unit tests must not require a GPU, NGC, VSS, or internet.

---

# 11. FastAPI service

Run the project-side adapter on a different port than the NVIDIA service.

Default:

```text
project adapter: 8100
NVIDIA VSS/Cosmos: 8000
```

Endpoints:

## Health

```http
GET /health
```

Response:

```json
{
  "service": "cosmos-vss-sidecar",
  "status": "ok",
  "backend": "vss",
  "backend_ready": true,
  "model": "cosmos-reason2"
}
```

## Analyze uploaded video

```http
POST /analyze/video
Content-Type: multipart/form-data
```

Fields:

```text
file
episode_id optional
```

Return `SemanticEpisode`.

## Analyze URL

```http
POST /analyze/url
```

```json
{
  "url": "https://...",
  "episode_id": "episode_001"
}
```

## Analyze already registered VSS asset

```http
POST /analyze/vss
```

```json
{
  "file_id": "...",
  "episode_id": "episode_001"
}
```

## Retrieve artifact

```http
GET /semantic/{semantic_id}
```

---

# 12. CLI

Implement:

```bash
uv run cosmos-vss doctor
```

Checks:

- config loads
- backend URL resolves
- `/health/ready`
- configured model appears in `/models`
- artifact directory writable

Recorded file:

```bash
uv run cosmos-vss analyze ./demo.mp4
```

Pair it logically with an existing episode:

```bash
uv run cosmos-vss analyze ./demo.mp4 \
  --episode-id episode_0007
```

Output:

```text
semantic_id: sem_...
task: pick_and_place
instruction: Pick up the red bottle and place it into the right basket.
artifact: artifacts/semantic/sem_.../semantic.json
```

JSON-only mode:

```bash
uv run cosmos-vss analyze ./demo.mp4 --json
```

---

# 13. Recorded video — required demo path

This is the path that must work first.

```text
demo.mp4
   ↓
cosmos-vss analyze
   ↓
VSS
   ↓
Cosmos Reason
   ↓
SemanticEpisode
   ↓
semantic.json
```

Do not block this on live streaming.

A participant can record the same physical demonstration independently using:

- Quest recording
- phone camera
- webcam capture
- OBS
- another MP4 source

This avoids modifying the existing hand-data collector.

---

# 14. Live stream — optional second path

VSS supports live stream processing.

For this project, treat live mode as an enhancement after recorded-video analysis is green.

Preferred live path:

```text
camera
  ↓
local encoder / RTSP publisher
  ↓
RTSP
  ↓
VSS
  ↓
Cosmos Reason
  ↓
streamed captions/events
```

Do not add WebRTC-to-RTSP conversion to the existing AR/VR collector.

Instead document an external bridge in:

```text
tools/webcam_to_rtsp.md
```

The bridge may use an independently launched media server and FFmpeg/GStreamer.

The VSS client should support:

```http
POST /v1/streams/add
GET  /v1/streams/get-stream-info
POST /v1/generate_captions
DELETE /v1/streams/delete/{stream_id}
```

When live mode is active, consume streamed responses and normalize completed chunks into semantic events.

---

# 15. VSS/Cosmos deployment on the NVIDIA machine

Keep NVIDIA deployment outside the application package.

Do not vendor or fork the complete NVIDIA VSS Blueprint into this repository.

Use the official VSS deployment separately and point the sidecar at it with `VSS_BASE_URL`.

Preflight:

```bash
nvidia-smi
docker --version
docker info
```

Then verify NGC credentials and the currently supported VSS deployment requirements from NVIDIA's documentation before pulling images.

After VSS is running:

```bash
curl http://localhost:8000/v1/health/ready
curl http://localhost:8000/v1/models
```

Only after both succeed should the project-side adapter be tested.

For the hackathon, prefer a direct VSS REST integration. Do not add NeMo Agent Toolkit unless a later feature actually requires a conversational multi-tool agent.

---

# 16. Model selection

Implement model selection as config, never hard-code it in business logic.

Recommended starting order:

```text
1. Cosmos Reason2 through the VSS Real-Time VLM service
2. Cosmos3 Nano Reasoner if that is the model already deployed in the selected VSS profile
3. direct Cosmos Reason NIM fallback for debugging
```

Example:

```bash
VSS_MODEL=cosmos-reason2
```

The service must expose the configured model in health/provenance output.

---

# 17. What semantic data is useful to downstream RL later

This feature does **not** modify RL.

However, produce data that a future fusion layer can use:

```text
episode_id
task_type
instruction
object roles
phase timestamps
success condition
spatial relationships
```

Example future join:

```text
LeRobot rows:
t=2.10 → wrist pose / joints / object pose
t=2.14 → wrist pose / joints / object pose
t=2.18 → wrist pose / joints / object pose

Semantic sidecar:
1.90–2.40 → phase="grasp"
```

A future adapter can align them by timestamp.

Do not implement that alignment here.

---

# 18. Demo UI

Build a minimal standalone debug page served by the new FastAPI service or a tiny static frontend.

Required layout:

```text
┌────────────────────────────────────────────────────┐
│ COSMOS / VSS — ROBOT DEMONSTRATION UNDERSTANDING │
├───────────────────────────┬────────────────────────┤
│                           │ Task                   │
│                           │ pick_and_place         │
│       video player        │                        │
│                           │ Instruction            │
│                           │ Move red bottle ...    │
│                           │                        │
│                           │ Objects                │
│                           │ red bottle             │
│                           │ right basket           │
├───────────────────────────┴────────────────────────┤
│ Timeline                                           │
│ approach → grasp → lift → transport → release     │
├────────────────────────────────────────────────────┤
│ Backend: VSS | Model: Cosmos Reason2 | READY      │
└────────────────────────────────────────────────────┘
```

This UI is not the Quest UI.

It is only for proving the Cosmos/VSS feature independently.

Do not edit the existing XR page to add this.

---

# 19. Tests

## Unit tests

### Schema validation

Reject:

- phase end before phase start
- duplicate object IDs
- relationship references to unknown objects
- malformed source types

### Model parser

Fixtures:

```text
valid plain JSON
JSON inside ```json fences
extra text before JSON
truncated JSON
unknown phase
missing objects
```

### Artifact writer

Verify:

- directory created
- semantic JSON validates
- source hash stable
- raw response preserved
- provenance written

### Mock analyzer

A fixture video does not need to be decoded for the mock test.

Ensure:

```text
input → mock analyzer → schema → artifact
```

works offline.

---

# 20. Integration tests

Mark GPU/NVIDIA tests:

```python
@pytest.mark.nvidia
```

They are skipped by default.

Test:

```text
1. VSS health ready
2. models endpoint includes configured model
3. upload a tiny MP4
4. run analysis
5. parse result
6. save SemanticEpisode
```

Do not make ordinary CI depend on VSS.

---

# 21. Acceptance criteria

## Gate A — feature isolation

Existing tests for AR/VR data collection and RL still pass without importing `cosmos_vss`.

No existing collector file needs to change.

No RL file needs to change.

## Gate B — standalone offline path

```bash
uv run pytest packages/cosmos-vss/tests -q
```

passes with the mock backend and no GPU.

## Gate C — backend health

```bash
uv run cosmos-vss doctor
```

returns success against the deployed NVIDIA service.

## Gate D — recorded video

Given a short pick-and-place MP4:

```bash
uv run cosmos-vss analyze demo.mp4 --episode-id episode_demo
```

writes a schema-valid semantic artifact.

## Gate E — useful output

The artifact must include:

- task type
- instruction
- manipulated object
- target
- at least one temporal phase
- success condition or explicit null
- ambiguity notes
- backend/model provenance

## Gate F — no control leakage

There must be no robot joint commands, servo commands, or direct actuation generated by Cosmos/VSS.

---

# 22. Failure behavior

If NVIDIA backend is unavailable:

```text
ERROR: configured VSS backend is not ready
```

not:

```text
falling back silently...
```

If Cosmos response is invalid:

```text
ERROR: semantic response failed schema validation
raw response preserved at ...
```

If the video is ambiguous:

keep the artifact but populate:

```json
{
  "ambiguity_notes": [
    "The target container is partially occluded."
  ]
}
```

Do not invent a missing target.

---

# 23. Logging

Use structured logs.

Each request logs:

```text
semantic_id
episode_id
backend
model
source hash prefix
latency
parse success/failure
artifact path
```

Never log NGC API keys.

---

# 24. Security/config

`.env.example`:

```bash
COSMOS_VSS_BACKEND=vss

VSS_BASE_URL=http://127.0.0.1:8000/v1
VSS_MODEL=cosmos-reason2

COSMOS_BASE_URL=http://127.0.0.1:8000/v1
COSMOS_MODEL=nvidia/cosmos-reason2-8b

COSMOS_VSS_ARTIFACT_DIR=artifacts/semantic
COSMOS_VSS_TIMEOUT_S=180
```

Do not commit:

```text
NGC_API_KEY
NVIDIA_API_KEY
tokens
credentials
```

---

# 25. Implementation order

## Phase 1 — package skeleton

Create package, config, schemas, artifact writer, mock analyzer.

Pass unit tests.

## Phase 2 — Cosmos prompt + parser

Create versioned robotics prompt.

Implement strict structured parsing and validation.

## Phase 3 — VSS REST adapter

Implement:

```text
health
models
upload/analyze recorded file
```

Do not implement live streams yet.

## Phase 4 — direct Cosmos NIM adapter

Implement as explicit fallback/debug backend.

## Phase 5 — FastAPI + CLI

Expose the standalone API and CLI.

## Phase 6 — real short demo

Use one 5–15 second pick-and-place video.

Verify meaningful output.

## Phase 7 — debug UI

Add the independent local demo page.

## Phase 8 — live RTSP

Only if recorded path is already reliable.

---

# 26. Do not do

Do not:

- rewrite the existing hand tracker
- rewrite the existing recorder
- alter `HumanEpisode`
- alter LeRobot columns
- alter RL rewards
- train a policy
- retarget hands to robot joints
- add Cosmos-generated actions directly to robot control
- modify CAD
- modify PCB
- modify the Quest implementation
- build a generic VSS search product
- add a vector database
- add RAG
- add a second agent framework just to say one is present
- make VSS a hard dependency for data collection
- make data collection a hard dependency for VSS

---

# 27. Hackathon demo

Run two independent windows.

## Window A — existing AR/VR collector

Shows:

```text
hand tracking
object interaction
recording
HumanEpisode / LeRobot data
```

## Window B — new Cosmos/VSS feature

Shows the same type of manipulation video and outputs:

```text
COSMOS/VSS

Task
  pick_and_place

Instruction
  Move the red bottle into the right basket.

Objects
  red bottle → manipulated object
  right basket → target

Timeline
  0.0 approach
  1.3 grasp
  2.0 lift
  3.1 transport
  5.7 release

Success
  bottle is inside basket
```

Then explain:

> The AR/VR system captures precise human and object trajectories. In parallel, VSS and Cosmos extract the semantic structure of the demonstration. They are deliberately stored independently so the same physical demonstration can later be paired with robot-specific training without coupling perception to one embodiment.

That is the intended separation.

---

# 28. Definition of done

The feature is done when:

```text
MP4
 ↓
standalone cosmos-vss command
 ↓
NVIDIA VSS
 ↓
Cosmos Reason
 ↓
validated SemanticEpisode JSON
 ↓
artifact saved
 ↓
visible in standalone debug UI
```

and none of the existing RL, CAD, PCB, or AR/VR collector implementation had to be modified.
