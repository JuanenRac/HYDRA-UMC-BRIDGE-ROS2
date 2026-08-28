<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Contribution guide
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Contributing

Keep this bridge a coordination layer: ROS 2 topics, services and actions must
not silently bypass HYDRA-UMC-SDK job gates or independent cell safety.

Before opening a change, run `build-test.bat` on Windows or `bash build-test.sh`
on Linux. Add a focused test for each state mapping or admission rule changed.
Hardware-dependent behavior must state its tested controller, interface and
safe failure mode; unverified hardware support must not be presented as ready.
