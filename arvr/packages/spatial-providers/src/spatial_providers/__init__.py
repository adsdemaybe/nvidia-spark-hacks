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
from .robot_provider import RobotProvider, get_configured_robot_provider

__all__ = [
    "AssetProvider",
    "FixtureAssetProvider",
    "FixtureRobotProvider",
    "RobotProvider",
    "UnknownAssetError",
    "UnknownRobotError",
    "get_configured_robot_provider",
]
