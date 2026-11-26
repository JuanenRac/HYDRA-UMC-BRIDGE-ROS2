# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - tests/test_signed_global_stop.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""With a key file a SAFE_STOP is obeyed as before and only a signed READY ends it."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hydra_umc_sdk.global_stop import DEFAULT_TOPIC, GlobalStopGuard, sign_announcement

from hydra_umc_bridge_ros2.mqtt_transport import GLOBAL_STOP_ENVIRONMENT_NAME

KEY = b"k" * 32


def announcement(state):
    return {"schema_version": "1.0", "state": state, "source": "test"}


class SignedGlobalStop(unittest.TestCase):
    def test_only_a_signed_ready_ends_a_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = Path(folder) / "stop.key"
            key_file.write_bytes(KEY)
            os.chmod(key_file, 0o600)
            base = f"HYDRA_UMC_{GLOBAL_STOP_ENVIRONMENT_NAME}_GLOBAL_STOP"
            with mock.patch.dict(os.environ, {base: "1", f"{base}_KEY_FILE": str(key_file)}):
                guard = GlobalStopGuard.from_environment(GLOBAL_STOP_ENVIRONMENT_NAME)
        guard.observe(json.dumps(announcement("SAFE_STOP")).encode())
        self.assertTrue(guard.blocks())
        guard.observe(json.dumps(announcement("READY")).encode())
        self.assertTrue(guard.blocks())
        guard.observe(json.dumps(sign_announcement(DEFAULT_TOPIC, announcement("READY"), KEY)).encode())
        self.assertFalse(guard.blocks())


if __name__ == "__main__":
    unittest.main()
