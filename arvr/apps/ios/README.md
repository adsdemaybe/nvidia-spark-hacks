# apps/ios — Andrew — `feat/ar-teach`, `feat/ar-follow`, `feat/ar-twin`, `feat/ar-correction`

Not yet started. Swift / SwiftUI / ARKit / RealityKit, per master plan
section 15. Does not need Spark/SSH access — develop against
`../../fixtures/ar-xr/` and `../../tools/mock_twin_server.py`.

Planned structure (master plan section 15):

```
Place/  Teach/  Replay/  Follow/  Twin/  Correct/  Spatial/  Networking/  Models/
```

Every value this app produces or consumes must be a `SpatialFrame`,
`TwinState`, `FollowState`, or `CorrectionEvent` from `ar-contracts`
(see `../../docs/CONTRACTS.md`) — no ad hoc JSON shapes.
