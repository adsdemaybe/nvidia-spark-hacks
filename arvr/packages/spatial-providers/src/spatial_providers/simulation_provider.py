"""Re-exports SimulationProvider/TaskSpec from ar_contracts.

These moved to ar_contracts (see its simulation_provider.py docstring for
why) once ar_datapipe's spatial_pipeline.py orchestrator needed to type
against this interface without spatial_providers -> ar_datapipe ->
spatial_providers becoming a circular package dependency. Kept as a
re-export here so `from spatial_providers import SimulationProvider,
TaskSpec` keeps working unchanged.
"""

from __future__ import annotations

from ar_contracts import SimulationProvider, TaskSpec

__all__ = ["SimulationProvider", "TaskSpec"]
