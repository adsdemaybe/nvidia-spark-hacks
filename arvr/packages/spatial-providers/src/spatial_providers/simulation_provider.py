"""Re-exports SimulationProvider/TaskSpec from ar_contracts.

These moved to ar_contracts (see its simulation_provider.py docstring for
why) once ar_datapipe's spatial_pipeline.py orchestrator needed to type
against this interface without spatial_providers -> ar_datapipe ->
spatial_providers becoming a circular package dependency. Kept as a
re-export here so `from spatial_providers import SimulationProvider,
TaskSpec` keeps working unchanged.
"""

from __future__ import annotations

import os

from ar_contracts import SimulationProvider, TaskSpec

__all__ = ["SimulationProvider", "TaskSpec", "get_configured_simulation_provider"]


def get_configured_simulation_provider() -> SimulationProvider:
    """Reads STRUCT_SIMULATION_PROVIDER (default "mujoco"). Same switch-point
    discipline as get_configured_robot_provider() -- no downstream code
    branches on which simulator produced a VerificationResult, this is the
    only place that distinction is allowed to exist.

    "isaac" requires packages/isaac-bridge/run_verify_server.py already
    running (SSH/Spark-only, see that package's README) -- this only
    constructs the WS *client*; a misconfigured/unreachable URL surfaces as
    IsaacVerifyServerUnavailable when replay_and_verify() is actually
    called, not here."""
    kind = os.environ.get("STRUCT_SIMULATION_PROVIDER", "mujoco")
    if kind == "mujoco":
        from .mujoco_simulation_provider import MuJoCoSimulationProvider

        return MuJoCoSimulationProvider()
    if kind == "isaac":
        from .isaac_simulation_provider import IsaacSimulationProvider

        return IsaacSimulationProvider()
    raise ValueError(f"unknown STRUCT_SIMULATION_PROVIDER: {kind!r}")
