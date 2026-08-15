# packages/isaac-bridge — Sky — `feat/ar-isaac-bridge` (SSH/Spark-only)

**Blocked**: Isaac Sim is not installed on the Spark (`gn100-dd0e`, checked
2026-08-15 — no `isaac*` paths, no `nvcc`). Do not start this package until
that's provisioned; see `../../STATE.md`.

Planned responsibility (master plan section 14B-C): connect to the running
Isaac Sim instance, read joint state / object transforms / task status /
trajectory, normalize into `TwinState` (`ar-contracts`), publish over
WebSocket at 20-60 Hz. Must be a drop-in replacement for
`tools/mock_twin_server.py` from the client's point of view — same wire
schema, different data source.
