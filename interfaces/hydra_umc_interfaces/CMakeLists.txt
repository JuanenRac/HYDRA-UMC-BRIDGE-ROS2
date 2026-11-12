# HYDRA-UMC-BRIDGE-ROS2 - hydra_umc_interfaces/CMakeLists.txt
# GPL-3.0-or-later - see LICENSE
cmake_minimum_required(VERSION 3.8)
project(hydra_umc_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/CellStatus.msg"
  "srv/InspectCell.srv"
  "action/ExecuteCellJob.action"
)

ament_package()
