<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Optional extensions
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Optional extensions of the bridge

The bridge does one thing by default: it gates a job and calls the safe-stop service of ROS 2 (see `BRIDGE_GUIDE.md`). Everything else that connects it to more of ROS 2 lives in
small modules called extensions. **Every extension is off by default**, is switched on in the `extensions` object of `docs/deploy/bridge.json`, and a section that is mistyped, has a
value of the wrong type or names an extension that does not exist stops the start of the service (`--check` finds it without opening anything).

```json
{
  "extensions": {
    "diagnostics": { "enabled": true }
  }
}
```

| Extension | Section | Direction | What it can do | Guide |
|---|---|---|---|---|
| Cell status | `cell_status` | HYDRA-UMC to ROS 2 | read only | `CELL_STATUS_FEED.md` |
| Diagnostics | `diagnostics` | ROS 2 to HYDRA-UMC | read only | `DIAGNOSTICS_FEED.md` |
| Fleet | `fleet` | HYDRA-UMC to ROS 2 and AMR | **can move a vehicle**: global stop, signed command, gate, named vehicles and stations only | `FLEET_ENTRY.md` |
| Fleet | `fleet` | HYDRA-UMC to ROS 2 and AMR | **can move a vehicle**: global stop, signed command, gate, named vehicles and stations only | `FLEET_ENTRY.md` |

## What the hook gives an extension

An extension is the module `ext_<name>.py` of the package with `parse(section)` (settings from the JSON, errors for anything unexpected) and `build(settings, bridge)` (the extension, or
`None` when `enabled` is false). `extensions.py` has the base class `BridgeExtension`:

* extra MQTT filters (`subscriptions()`), subscribed again after every reconnect, together with the announcement of the global stop when the bridge follows it;
* the topics it answers (`claims`), and which of them are handled on the network thread (`inline`, for cheap feeds; this is how a retained message reaches an extension, since the shared
  dispatcher drops retained messages for the command lane);
* `moving_verbs` and `job_verbs`: the command topics below `hydra/bridges/ros2/` that are refused while the global stop of the server is active (an abort job still passes);
* `start(node, publish)` after the node is spinning and `stop()` at the end. An enabled extension that cannot start (a ROS 2 message package is missing) stops the service instead of
  being skipped, as the bridge already does when it cannot observe the state topic.

The message size limit (65536 bytes), the refusal of a retained command and the rule that a message never ends the loop apply to extensions as they do to the commands of the bridge.

## What the extensions are not

They are not a second gate. A job still passes the gate of the SDK (`evaluate_job`) with the state of the machine that the bridge itself observed. Evidence for every extension is
`simulator`: host tests and a ROS 2 container with nodes of its own, never a robot.
