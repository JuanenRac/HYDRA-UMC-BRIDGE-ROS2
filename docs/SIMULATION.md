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
| `tests/test_coordinator.py` | the coordinator and its gate |
| `tests/test_mqtt_transport.py` | the topic dispatch, QoS, idempotency and limits without a broker |
| `tests/test_broker_integration.py` | the same dispatch against a real MQTT broker; skipped unless the broker checkout is present |
| `tests/test_global_stop_broker.py` | the global stop over a real broker; skipped unless the broker checkout is present |

The workflow also runs `python tools/inspect_interface_plan.py --verify-fixture`, so the interface plan fixture cannot drift from the code.

The broker scenarios start a real MQTT broker from a checkout of HYDRA-UMC-MQTT-BROKER (`HYDRA_UMC_TEST_BROKER_DIR`); the simulation job does not install it, so
those cases skip themselves there and run in the normal test run on a machine that has the broker.

## What it cannot show

A ROS 2 distribution, real nodes, topics and services, and any real robot behind them; the real transport is not exercised here. The hardware row of the status table therefore stays at "simulated" until a person has run the bridge on a real setup.
