# AR/XR Spatial Contracts — frozen v1.0

Status: **frozen for Phase 0**. Changing any field here is a contract change
and requires review (STRUCT master plan rule 85.15). Additive, backward
compatible changes bump a minor doc note here; breaking changes bump
`schema_version` and require updating every model in
`packages/ar-contracts/src/ar_contracts/`.

## Coordinate convention

| | |
|---|---|
| Handedness | right-handed |
| Up axis | Z |
| Units | meters |
| Orientation | quaternion, **[x, y, z, w]** order |
| Time | nanoseconds since Unix epoch (`timestamp_ns`, monotonically non-decreasing within one episode/stream) |
| Forward (Follow mode only) | local **+X** — see `follow.py`, this is a Sky judgment call the spec left unstated (ROS REP-103 convention), flagged for review |

Every `frame` value is one of:

```
device_frame   ar_world   struct_world   robot_base   end_effector
```

Device adapters (ARKit, Quest controller, MediaPipe, desktop mock) MUST
convert into this convention before a value reaches any contract in this
package. No downstream robotics code may depend on a device-native frame
(master plan section 5).

## The seven contracts

| Contract | Spec section | Source file |
|---|---|---|
| `SpatialFrame` | 29 | `spatial_frame.py` |
| `SpatialEpisode` | 30 | `spatial_episode.py` |
| `TwinState` | 31 | `twin_state.py` |
| `FollowState` | 32 | `follow_state.py` |
| `CorrectionEvent` | 33 | `correction_event.py` |
| `SceneManifest` | 34 | `scene_manifest.py` |
| `VerificationResult` | named in 13A, no literal example given — derived, see docstring in `verification_result.py` | `verification_result.py` |

All are pydantic `FrozenModel`s: immutable after construction, `extra="forbid"`
(unknown fields fail loudly), `schema_version: Literal["1.0"]` (unknown
versions are rejected at the type level, no custom check needed).

## Validation rules enforced structurally (not by convention)

- `position_m` / `orientation_xyzw` — every component must be finite (no NaN/Inf).
- `orientation_xyzw` — must be a unit quaternion, norm within `1e-2` of `1.0`
  (loose enough for rounded/float32 data — the spec's own SpatialFrame
  example in section 29 has norm 0.997697).
- `timestamp_ns` — non-negative.
- `SpatialEpisode.events` — must be ordered by non-decreasing `timestamp_ns`.
- `SpatialEpisode.episode_id` — must parse as a UUID.
- `SpatialFrame.gripper` — `[0.0, 1.0]` or `None`.
- `FollowState.desired_follow_distance_m` — finite and `> 0`.
- `VerificationResult` — `status="rejected"` requires `rejection_reason`
  (master plan section 63, rule 85.10: "preserve rejected episode reasons").
  `status="accepted"` requires `dataset_id`. There is no free-text "verdict"
  field — rule 85.9 ("never mark verification success from an LLM
  judgment") is enforced by the type, not a policy.

## Deviations from the literal spec text (flagged for review)

- `VerificationResult` has no literal JSON example in the master plan; the
  shape here is derived from the `GET /xr/episodes/{id}` example (section
  36) and the acceptance-gate checklist in the demo script (section 75).
- The fixture pack (`fixtures/ar-xr/`) writes `sample_episode.jsonl`
  instead of `sample_episode.parquet` — pyarrow isn't a dependency of the
  fixture generator yet. A real recording pipeline (Phase 4) should write
  Parquet per spec section 35; realsim's r2s-core already has a Parquet
  artifact path worth reusing as a reference, not reinventing.
- `fixtures/ar-xr/{table,cube,bin,robot}.glb` are not fabricated — see
  `fixtures/ar-xr/ASSETS_TODO.md`.

## Using this package

```python
from ar_contracts import SpatialFrame, compute_follow_target

frame = SpatialFrame.model_validate(json_payload)  # raises on any violation above
```
