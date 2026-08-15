# packages/xr-web — Andrew — `feat/xr-web-adapter` (optional, stretch)

Not started. Only pick this up after the phone workflows (`apps/ios/`) are
stable (master plan section 28). TypeScript / React / Three.js / WebXR /
`@react-three/xr`. A tracked XR controller becomes another SpatialAdapter —
it must emit the same `SpatialFrame` the phone does (`ar-contracts`), and
must not create a second robot backend.
