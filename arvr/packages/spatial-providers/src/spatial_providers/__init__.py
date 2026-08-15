"""spatial_providers — RobotProvider/AssetProvider/HandProvider/
SimulationProvider interfaces + Fixture/Mock/MuJoCo implementations for the
Shadow Robot Spatial Demonstration Pipeline (spec section 7). No downstream
code should ever import a Fixture*/Mock*/MuJoCo* implementation directly
except at the provider-selection boundary -- everything else should type
against the ABCs re-exported here.
"""

from .asset_provider import AssetProvider
from .fixture_asset_provider import FixtureAssetProvider, UnknownAssetError
from .fixture_robot_provider import FixtureRobotProvider, UnknownRobotError
from .hand_provider import HandProvider
from .isaac_simulation_provider import IsaacSimulationProvider, IsaacVerifyServerUnavailable
from .mock_hand_provider import MockHandProvider
from .mujoco_simulation_provider import MuJoCoSimulationProvider
from .robot_provider import RobotProvider, get_configured_robot_provider
from .simulation_provider import (
    SimulationProvider,
    TaskSpec,
    get_configured_simulation_provider,
)

__all__ = [
    "AssetProvider",
    "FixtureAssetProvider",
    "FixtureRobotProvider",
    "HandProvider",
    "IsaacSimulationProvider",
    "IsaacVerifyServerUnavailable",
    "MockHandProvider",
    "MuJoCoSimulationProvider",
    "RobotProvider",
    "SimulationProvider",
    "TaskSpec",
    "UnknownAssetError",
    "UnknownRobotError",
    "get_configured_robot_provider",
    "get_configured_simulation_provider",
]
