# HYDRA-UMC-BRIDGE-ROS2 - launch file of the bridge (GPL-3.0-or-later - see LICENSE)
"""`ros2 launch docs/deploy/hydra_umc_bridge_ros2.launch.py` starts the bridge service with the ROS parameter file and the configuration file that sit next to it.

Arguments: `config:=<path to bridge.json>` and `params:=<path to bridge_params.yaml>` (the defaults are the files of this repository). The service creates the node, spins it
on an executor and connects to the MQTT broker named in the configuration. Nothing here starts a robot: the bridge only gates jobs and calls the safe stop service.
"""

from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("config", default_value=str(HERE / "bridge.json"), description="the service configuration (MQTT broker, node name, switches)"),
            DeclareLaunchArgument("params", default_value=str(HERE / "bridge_params.yaml"), description="the ROS 2 parameter file of the bridge node"),
            ExecuteProcess(
                cmd=["python3", "-m", "hydra_umc_bridge_ros2", "--config", LaunchConfiguration("config"), "--ros-args", "--params-file", LaunchConfiguration("params")],
                cwd=str(ROOT),
                additional_env={"PYTHONPATH": str(ROOT / "src")},
                output="screen",
                name="hydra_umc_bridge_ros2",
            ),
        ]
    )
