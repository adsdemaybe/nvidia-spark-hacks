"""ar_sim — local rigid-body physics behind the live Twin stream.

Not the authoritative twin (OpenUSD in Isaac Sim is, once wired — see
STATE.md). Ported from Andrew's independent `arxr-sim` package during the
arvr/arxr consolidation. See ar-sim/README.md for the ik.py vs
ar_datapipe.retarget distinction.
"""

from .director import PickAndPlaceDirector, Waypoint
from .ik import IKResult, solve_ik
from .twin import MujocoTwinSource

__all__ = [
    "IKResult",
    "MujocoTwinSource",
    "PickAndPlaceDirector",
    "Waypoint",
    "solve_ik",
]
