# Simulation bench

The ROS 2 bridge needs a full ROS 2 distribution for its real transport, which a CI runner does not have and which the tests deliberately do not fake. The simulation bench therefore covers what can be proven without ROS 2: the interface plan, the coordinator and its gate, the MQTT dispatch and the way the transport degrades when ROS 2 is missing.

The simulation bench replaces the hardware with the interface-plan fixtures (`tests/fixtures`) and the honest degradation paths of the ROS 2 transport. It proves the parsing, the gating, the transports and the failure paths of the bridge; it says nothing about
a real machine, real timing or real safety functions.

## Run it

```
python -m unittest -v <modules>
```

from the `tests` folder, with `src` of this repository and `clients/python/src` of the SDK on `PYTHONPATH` (the same layout `python tools/build_test.py` uses). The
workflow named "Simulation bench" does exactly this on a clean Linux runner on every push and pull request, by hand and once a week, with nothing but Python.

## What it runs

| File | What it exercises |
|------|-------------------|
| `tests/test_rclpy_transport.py` | the transport refuses cleanly and says why when `rclpy` is missing; nothing is faked at the message level |
| `tests/test_service_and_safety.py` | the service configuration and its ROS parameters, the 2 s expiry of the machine state, an `ABORT` job that calls the safe-stop service, the shared job intake, and the launch file, parameter file and systemd unit |
| `tests/test_extensions.py` | the hook for optional extensions: the section parser, routing of claimed topics, the global stop on a moving verb, start and stop in `run_forever()` |
| `tests/test_ext_diagnostics.py` | the diagnostics feed: summary, limits, silence, cleaning of texts, a seeded random stream, and that it claims no command topic |
| `tests/test_ext_cell_status.py` | the cell status feed: how both states are worked out, every fail-closed path, the limits, a seeded random stream, and that nothing is published back |
| `tests/ros2/test_ext_cell_status_ros2.py` | against a real rclpy in the container: real `CellStatus` messages for a seeded sequence with faults, a subscriber that joins late, and that the node has no subscription, service or client |
| `tests/ros2/test_ext_diagnostics_ros2.py` | against a real rclpy in the container: a seeded stream of real `DiagnosticArray` messages, injected faults, silence, and that the node gains no publisher, service or client |
| `tests/ros2/test_real_ros2.py` | against a real rclpy in the ROS 2 container of the workflow "ROS 2 bench" (skipped elsewhere): the generated interface types, the state topic and its expiry, the safe-stop service, the entry point |
| `tests/test_coordinator.py` | the coordinator and its gate |
| `tests/test_mqtt_transport.py` | the topic dispatch, QoS, idempotency and limits without a broker |
| `tests/test_broker_integration.py` | the same dispatch against a real MQTT broker; skipped unless the broker checkout is present |
| `tests/test_global_stop_broker.py` | the global stop over a real broker; skipped unless the broker checkout is present |
| `tests/test_service_main_faults.py` | the start-up of the service when things go wrong: damaged configuration, missing or empty secret file, ROS 2 not installed, a transport loop that raises or ends (exit codes for systemd, the secret never printed) |

The workflow also runs `python tools/inspect_interface_plan.py --verify-fixture`, so the interface plan fixture cannot drift from the code, and `python tools/inspect_interface_plan.py --verify-contract`, which reads the `.msg`, `.srv` and `.action` files of `interfaces/hydra_umc_interfaces` and compares them with the SDK and with `tests/fixtures/interface-contract-v1.json`.

The broker scenarios start a real MQTT broker from a checkout of HYDRA-UMC-MQTT-BROKER (`HYDRA_UMC_TEST_BROKER_DIR`); the simulation job does not install it, so
those cases skip themselves there and run in the normal test run on a machine that has the broker.

## What it cannot show

A ROS 2 distribution, real nodes, topics and services, and any real robot behind them; the real transport is not exercised here. The hardware row of the status table therefore stays at "simulated" until a person has run the bridge on a real setup.
