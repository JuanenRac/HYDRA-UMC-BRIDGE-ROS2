# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Cell status extension against a real ROS 2 (container workflow, skipped where rclpy is missing)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A real rclpy node publishes `hydra_umc_interfaces/msg/CellStatus` for the cells the server announces; a real subscriber, also one that joins late, reads them.

The server is played by the test (the MQTT messages are handed to the extension as the transport would): a seeded random sequence with injected faults. Evidence: a ROS 2 container with the
nodes of this test, never a robot, a controller or the real server.
"""

from __future__ import annotations

import json
import random
import threading
import time
import unittest

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile
except ImportError:  # no ROS 2 on this host
    rclpy = None

try:
    from hydra_umc_interfaces.msg import CellStatus
except ImportError:  # the interface package is not built/sourced
    CellStatus = None

from hydra_umc_bridge_ros2.ext_cell_status import CellStatusExtension, CellStatusSettings

NEEDS_INTERFACES = unittest.skipIf(rclpy is None or CellStatus is None, "rclpy or the hydra_umc_interfaces package is not available")
SEED = 20260
P = "hydra/swarm"


def wait_for(condition, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def setUpModule() -> None:
    if rclpy is not None and not rclpy.ok():
        rclpy.init()


def tearDownModule() -> None:
    if rclpy is not None and rclpy.ok():
        rclpy.shutdown()


def robot(online=True, playing=False, paused=False) -> bytes:
    return json.dumps({"online": online, "playbackState": {"isPlaying": playing, "isPaused": paused}}).encode()


class _Listener:
    """A node that subscribes (transient local, like a node that started late) and keeps the last message of each cell."""

    def __init__(self, name: str, robots) -> None:
        self.node = rclpy.create_node(name)
        self.last: dict[str, CellStatus] = {}
        self.count = 0
        qos = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        for robot_name in robots:
            self.node.create_subscription(CellStatus, f"/hydra_umc/cells/{robot_name}/status", lambda message, who=robot_name: self.heard(who, message), qos)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.thread.start()

    def heard(self, who: str, message) -> None:
        self.last[who] = message
        self.count += 1

    def close(self) -> None:
        self.executor.shutdown()
        self.thread.join(2.0)
        self.node.destroy_node()


@NEEDS_INTERFACES
class RealCellStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.node = rclpy.create_node("hy_cells_bridge")
        self.addCleanup(self.node.destroy_node)
        self.extension = CellStatusExtension(CellStatusSettings(enabled=True, max_cells=4))
        self.extension.start(self.node, lambda *a: None)
        self.addCleanup(self.extension.stop)

    def server(self, topic: str, payload: bytes) -> None:
        self.extension.on_message(f"{P}/{topic}", payload, True)

    def listener(self, name: str, robots) -> _Listener:
        listener = _Listener(name, robots)
        self.addCleanup(listener.close)
        return listener

    def settled(self, listener: _Listener) -> bool:
        records = self.extension.feed.records()
        return all(name in listener.last and (listener.last[name].cell_state, listener.last[name].machine_state) == (record.cell_state, record.machine_state) for name, record in records.items())

    def test_a_seeded_sequence_with_faults_ends_with_every_subscriber_seeing_what_the_extension_derived(self) -> None:
        names = [f"robot_{index}" for index in range(6)]
        early = self.listener("hy_cells_early", names)
        rng = random.Random(SEED)
        self.server("status", b"online")
        self.server("safety/estop", json.dumps({"state": "READY"}).encode())
        for _ in range(120):
            choice = rng.randrange(6)
            if choice == 0:
                self.server("safety/estop", rng.choice([b'{"state": "SAFE_STOP"}', b'{"state": "READY"}', b"{broken"]))
            elif choice == 1:
                self.server("status", rng.choice([b"online", b"offline"]))
            else:
                self.server(f"{rng.choice(names)}/state", rng.choice([robot(True, True, False), robot(True, True, True), robot(True), robot(False), b"", b"{broken"]))
        self.server("safety/estop", b'{"state": "READY"}')
        self.server("status", b"online")
        self.assertTrue(wait_for(lambda: self.settled(early)), "a subscriber did not end with the state derived by the extension")
        self.assertGreater(self.extension.feed.dropped_cells, 0)  # six cells were announced and four are kept
        for message in early.last.values():
            self.assertIn(message.cell_state, (CellStatus.CELL_READY, CellStatus.CELL_INHIBITED, CellStatus.CELL_SAFE_STOP))
            self.assertIn(message.machine_state, (CellStatus.MACHINE_IDLE, CellStatus.MACHINE_RUNNING, CellStatus.MACHINE_HOLDING, CellStatus.MACHINE_OFFLINE))
            self.assertEqual(message.job_id, "")
        late = self.listener("hy_cells_late", names)
        self.assertTrue(wait_for(lambda: self.settled(late)), "a node that started late did not see the last state")

    def test_a_global_stop_reaches_ros_and_ready_ends_it(self) -> None:
        listener = self.listener("hy_cells_stop", ["robot_1"])
        self.server("status", b"online")
        self.server("safety/estop", b'{"state": "READY"}')
        self.server("robot_1/state", robot(True, True))
        self.assertTrue(wait_for(lambda: "robot_1" in listener.last and listener.last["robot_1"].machine_state == CellStatus.MACHINE_RUNNING))
        self.server("safety/estop", b'{"state": "SAFE_STOP"}')
        self.assertTrue(wait_for(lambda: listener.last["robot_1"].cell_state == CellStatus.CELL_SAFE_STOP))
        self.assertEqual(listener.last["robot_1"].machine_state, CellStatus.MACHINE_SAFE_STOP)
        self.server("safety/estop", b'{"state": "READY"}')
        self.assertTrue(wait_for(lambda: listener.last["robot_1"].cell_state == CellStatus.CELL_READY))

    def test_the_node_only_publishes_it_has_no_subscription_service_or_client(self) -> None:
        self.server("status", b"online")
        self.server("robot_1/state", robot())
        name, space = self.node.get_name(), self.node.get_namespace()
        subscribed = {topic for topic, _ in self.node.get_subscriber_names_and_types_by_node(name, space)}
        self.assertTrue(subscribed <= {"/parameter_events"}, subscribed)  # nothing of ours
        clients = [topic for topic, _ in self.node.get_client_names_and_types_by_node(name, space)]
        self.assertEqual(clients, [])
        services = [topic for topic, _ in self.node.get_service_names_and_types_by_node(name, space)]
        self.assertTrue(all("parameter" in topic for topic in services), services)
        published = {topic for topic, _ in self.node.get_publisher_names_and_types_by_node(name, space)}
        self.assertIn("/hydra_umc/cells/robot_1/status", published)
        self.assertEqual({topic for topic in published if topic.startswith("/hydra_umc")}, {"/hydra_umc/cells/robot_1/status"})


if __name__ == "__main__":
    unittest.main()
