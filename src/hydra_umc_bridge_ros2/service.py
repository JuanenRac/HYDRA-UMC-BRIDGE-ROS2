# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - service entry point (python -m hydra_umc_bridge_ros2)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Start the ROS 2 bridge as a service: read the configuration, create the rclpy node, spin it on an executor of its own and serve the MQTT topics.

`python -m hydra_umc_bridge_ros2 --config docs/deploy/bridge.json` (or the launch file and the systemd unit next to it in `docs/deploy/`) does what `run_forever()` needs done by hand:
it creates the node, takes the two ROS parameters `observed_max_age_s` and `abort_calls_safe_stop` from the ROS parameter system when a parameter file is given
(`--ros-args --params-file docs/deploy/bridge_params.yaml`; the value in the file of the configuration is the default), builds the bridge with `node_is_spinning=True`, and
`run_forever()` spins the node on a `SingleThreadedExecutor` in the background. Everything else comes from `docs/deploy/bridge.json`; the MQTT password is read from the file the
configuration names, never from the command line. `--check` validates the configuration and exits without opening anything. Not run against a real ROS 2 graph or broker.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Callable

from hydra_umc_sdk.bridge_contract import CellState, MachineState
from hydra_umc_sdk.cell_state_source import CellStateSource, CellStateSourceError, config_from_environment
from hydra_umc_sdk.job_intake import IdempotencyStore

from .config_access import (
    DEFAULT_PARAMS_PATH,
    DIRECTORY_NAME,
    LEGACY_CONFIG_PATH,
    LEGACY_PARAMS_PATH,
    SERVICE_GROUP,
    check_access_all,
    ensure_directory,
    group_exists,
    migrate_legacy_config,
    permission_problem,
)
from .extensions import ExtensionConfigError, build_extensions, parse_sections
from .mqtt_transport import DEFAULT_OBSERVED_MAX_AGE_SECONDS, Ros2MqttBridge, run_forever
from .rclpy_transport import create_ros2_node
from .server_identity import identity_key_from
from .settings_http import Sros2Settings, start_settings_server

_LOG = logging.getLogger(__name__)
CONFIG_ENVIRONMENT = "HYDRA_UMC_ROS2_CONFIG"
DEFAULT_CONFIG_PATH = "docs/deploy/bridge.json"


class ConfigError(ValueError):
    """The service configuration cannot be used (a mistyped key is an error, never a silent default)."""


@dataclass(frozen=True)
class MqttConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    client_id: str = "hydra-umc-bridge-ros2"
    username: str = ""
    password_file: str = ""
    max_connect_attempts: int = 5
    connect_retry_delay_seconds: float = 2.0


@dataclass(frozen=True)
class NodeConfig:
    name: str = "hydra_umc_bridge_ros2"
    # seconds the last state of the machine counts for; the publisher of the state topic must repeat it at least this often. 0 switches the limit off
    observed_max_age_s: float = DEFAULT_OBSERVED_MAX_AGE_SECONDS
    # an ABORT job calls the real safe stop service
    abort_calls_safe_stop: bool = True
    # keeps the idempotency keys across a restart; empty keeps them in memory
    idempotency_file: str = ""


@dataclass(frozen=True)
class SecurityConfig:
    # off by default so that a deployment without a ROS 2 keystore keeps starting; on, the service refuses to start unless SROS2 is enabled, enforced and has a keystore
    require_sros2: bool = False


@dataclass(frozen=True)
class ServiceConfig:
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    node: NodeConfig = field(default_factory=NodeConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    # the parsed settings of the optional extensions named in the file (extensions.py); none unless it names them
    extensions: dict[str, object] = field(default_factory=dict)


def _section(raw: object, kind: type, name: str):
    if raw is None:
        return kind()
    if not isinstance(raw, dict):
        raise ConfigError(f"'{name}' must be a JSON object")
    known = {item.name for item in fields(kind)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown keys in '{name}': {', '.join(unknown)}")
    defaults = kind()
    for key, value in raw.items():
        wanted = type(getattr(defaults, key))
        if wanted is float and isinstance(value, int) and not isinstance(value, bool):
            continue
        if not isinstance(value, wanted) or (wanted is int and isinstance(value, bool)):
            raise ConfigError(f"'{name}.{key}' must be a {wanted.__name__}")
    return kind(**raw)


def load_service_config(path: str | Path | None) -> ServiceConfig:
    """Read the service configuration; no file gives the defaults (a local broker, the conservative switches)."""
    if path is None:
        return ServiceConfig()
    try:
        is_file = Path(path).is_file()
    except PermissionError as error:  # a directory on the way that the account cannot enter
        raise ConfigError(f"cannot read {path}: {permission_problem(str(path)) or error}") from error
    if not is_file:
        # a file that is not there gives the defaults; one hidden by a permission must not look like that
        hidden = permission_problem(str(path))
        if hidden:
            raise ConfigError(f"cannot read {path}: {hidden}")
        return ServiceConfig()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError("the configuration must be a JSON object")
    unknown = sorted(set(raw) - {"mqtt", "node", "security", "extensions"})
    if unknown:
        raise ConfigError(f"unknown keys: {', '.join(unknown)}")
    try:
        extension_settings = parse_sections(raw.get("extensions"))
    except ExtensionConfigError as error:
        raise ConfigError(str(error)) from error
    config = ServiceConfig(
        _section(raw.get("mqtt"), MqttConfig, "mqtt"),
        _section(raw.get("node"), NodeConfig, "node"),
        _section(raw.get("security"), SecurityConfig, "security"),
        extension_settings,
    )
    if not config.mqtt.host or not 0 < config.mqtt.port < 65536:
        raise ConfigError("'mqtt.host' and 'mqtt.port' must name a broker")
    if config.mqtt.max_connect_attempts < 1 or config.mqtt.connect_retry_delay_seconds < 0:
        raise ConfigError("'mqtt' connection retries must be at least 1 attempt and a delay of 0 or more")
    problem = _node_problem(config.node)
    if problem:
        raise ConfigError(problem)
    return config


def sros2_problems(env: Mapping[str, str]) -> list[str]:
    """Why the ROS 2 security (SROS2) is not fully on in this environment, from the variables ROS 2 itself reads: ROS_SECURITY_ENABLE, ROS_SECURITY_STRATEGY and ROS_SECURITY_KEYSTORE.

    Empty when security is enabled, enforced (a node that cannot authenticate does not start, instead of falling back to plain traffic) and the keystore directory holds the `enclaves`.
    """
    problems: list[str] = []
    if env.get("ROS_SECURITY_ENABLE", "").strip().lower() != "true":
        problems.append("ROS_SECURITY_ENABLE is not true")
    if env.get("ROS_SECURITY_STRATEGY", "").strip().lower() != "enforce":
        problems.append("ROS_SECURITY_STRATEGY is not Enforce")
    keystore = env.get("ROS_SECURITY_KEYSTORE", "").strip()
    if not keystore:
        problems.append("ROS_SECURITY_KEYSTORE is not set")
    elif not (Path(keystore) / "enclaves").is_dir():
        problems.append("ROS_SECURITY_KEYSTORE has no 'enclaves' directory")
    return problems


def check_security(security: SecurityConfig, env: Mapping[str, str]) -> None:
    """With `security.require_sros2` the service does not start unless SROS2 is on; without it nothing is checked (the traffic of the node is then as plain as before)."""
    if not security.require_sros2:
        return
    problems = sros2_problems(env)
    if problems:
        raise ConfigError("'security.require_sros2' is on and SROS2 is not: " + "; ".join(problems))


def _node_problem(node: NodeConfig) -> str | None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.name):
        return "'node.name' must be a ROS 2 node name (letters, digits and underscores)"
    if not math.isfinite(node.observed_max_age_s) or node.observed_max_age_s < 0:
        return "'node.observed_max_age_s' must be a number of seconds, 0 or more (0 switches the limit off)"
    return None


def apply_ros_parameters(node: object, config: NodeConfig) -> NodeConfig:
    """The node configuration with the ROS parameters of the node on top: `observed_max_age_s` and `abort_calls_safe_stop` declared with the configured values as defaults.

    A parameter file given with `--ros-args --params-file` overrides them; a value of the wrong type or out of range is an error, so a mistyped file never changes the safety behaviour silently.
    """
    age = node.declare_parameter("observed_max_age_s", float(config.observed_max_age_s)).value  # type: ignore[attr-defined]
    abort = node.declare_parameter("abort_calls_safe_stop", bool(config.abort_calls_safe_stop)).value  # type: ignore[attr-defined]
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        raise ConfigError("the ROS parameter observed_max_age_s must be a number")
    if not isinstance(abort, bool):
        raise ConfigError("the ROS parameter abort_calls_safe_stop must be true or false")
    merged = replace(config, observed_max_age_s=float(age), abort_calls_safe_stop=abort)
    problem = _node_problem(merged)
    if problem:
        raise ConfigError(problem.replace("'node.", "the ROS parameter ").replace("'", ""))
    return merged


def _read_secret(path: str, what: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8-sig").strip()  # a byte order mark is not part of the secret
    except OSError as error:
        raise ConfigError(f"cannot read the {what} file: {error.strerror or error}") from error
    except UnicodeDecodeError:
        raise ConfigError(f"cannot read the {what} file: it is not UTF-8 text") from None
    if not value:
        raise ConfigError(f"the {what} file is empty")
    return value


def build_bridge(config: ServiceConfig, node_factory: Callable[[str], object] = create_ros2_node) -> Ros2MqttBridge:
    """The node and the bridge around it. The node factory is injectable for the tests; it raises RuntimeError when rclpy is not installed."""
    # where the cell is read besides what the machine says (HYDRA_UMC_ROS2_CELL_STATE_SOURCE and its companions, the shared readers of the SDK); built first: a source that cannot be used stops the start
    try:
        source = config_from_environment("ROS2")
        reader = CellStateSource(source) if source is not None else None
    except CellStateSourceError as error:
        raise ConfigError(f"the cell state source cannot be used: {error}") from error
    # a bridge that sends motion goals does not take the cell for READY because nobody says otherwise
    motion = bool(getattr(config.extensions.get("motion"), "enabled", False))
    node = node_factory(config.node.name)
    settings = apply_ros_parameters(node, config.node)
    holder: list[Ros2MqttBridge] = []

    def cell_state() -> CellState:
        # the cell is as ready as the machine says it is: a faulted or stopped machine is a faulted or stopped cell (the gate still needs the machine IDLE to start work)
        observed = holder[0]._observed if holder else None
        if observed is MachineState.FAULT:
            return CellState.FAULT
        if observed is MachineState.SAFE_STOP:
            return CellState.SAFE_STOP
        if reader is not None:
            return CellState(reader.read())
        return CellState.INHIBITED if motion else CellState.READY

    store = IdempotencyStore(settings.idempotency_file) if settings.idempotency_file else IdempotencyStore()
    bridge = Ros2MqttBridge(
        node,  # type: ignore[arg-type]
        cell_state,
        store,
        node_is_spinning=True,
        observed_max_age_seconds=settings.observed_max_age_s or None,
        abort_calls_safe_stop=settings.abort_calls_safe_stop,
    )
    bridge.cell_source = source.source if source is not None else "none"
    holder.append(bridge)
    build_extensions(config.extensions, bridge)
    return bridge


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydra_umc_bridge_ros2", description="HYDRA-UMC ROS 2 bridge service (ROS arguments such as --ros-args --params-file are passed on to rclpy)")
    parser.add_argument("--config", default=os.environ.get(CONFIG_ENVIRONMENT, DEFAULT_CONFIG_PATH), help=f"configuration file (default: ${CONFIG_ENVIRONMENT} or {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--check", action="store_true", help="read and validate the configuration, then exit")
    parser.add_argument("--settings-port", type=int, default=0, help="open the route by which HYDRA-UMC-SERVER reads (and, with --allow-settings-change, changes) security.require_sros2 on this port; 0 (the default) keeps it closed")
    parser.add_argument("--settings-host", default="127.0.0.1", help="the address of that route (default: loopback)")
    parser.add_argument("--identity-key-file", default=None, help="the file with the signing key of HYDRA-UMC-SERVER that the route needs (or $HYDRA_UMC_BRIDGE_ROS2_IDENTITY_KEY_FILE)")
    parser.add_argument("--allow-settings-change", action="store_true", help="let an administrator of the SERVER change security.require_sros2 in the configuration file through that route (it applies at the next restart)")
    parser.add_argument("--prepare-config", action="store_true", help="run as root before the service: set the directory of the service to mode 0750 and copy the configuration file and the parameter file of the earlier place there once, never overwriting and never deleting; exit 0 whether it copied, kept or found nothing")
    parser.add_argument("--check-access", action="store_true", help="check that the account that runs this command can read --config and --params-path, then exit (0 when readable, 2 with the reason when not); an installer runs it as the service account")
    parser.add_argument("--legacy-config", default=LEGACY_CONFIG_PATH, help=f"the earlier place of the configuration file, used by --prepare-config (default: {LEGACY_CONFIG_PATH})")
    parser.add_argument("--params-path", default=DEFAULT_PARAMS_PATH, help=f"the ROS 2 parameter file of the service, read by --prepare-config and --check-access (default: {DEFAULT_PARAMS_PATH})")
    parser.add_argument("--legacy-params", default=LEGACY_PARAMS_PATH, help=f"the earlier place of the parameter file, used by --prepare-config (default: {LEGACY_PARAMS_PATH})")
    args, _ros_arguments = parser.parse_known_args(argv)
    if args.check_access:
        code, message = check_access_all([args.config, args.params_path])
        print(message, file=sys.stderr if code else sys.stdout)
        return code
    if args.prepare_config:
        group = SERVICE_GROUP if group_exists(SERVICE_GROUP) else ""
        try:
            if Path(args.config).parent.name == DIRECTORY_NAME:  # the directory of the service, never a directory the operator named
                ensure_directory(str(Path(args.config).parent), group)
            outcomes = [
                migrate_legacy_config(args.legacy_config, args.config, group),
                migrate_legacy_config(args.legacy_params, args.params_path, group),
            ]
        except OSError as error:
            print(f"hydra-umc-bridge-ros2: prepare-config: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        print(f"hydra-umc-bridge-ros2: prepare-config: configuration {outcomes[0]} ({args.config}), parameters {outcomes[1]} ({args.params_path})")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_service_config(args.config)
        check_security(config.security, os.environ)
        password = _read_secret(config.mqtt.password_file, "MQTT password") if config.mqtt.password_file else None
        if args.check:
            print(f"CONFIG=PASS {args.config}")
            return 0
        identity_key = None
        if args.settings_port:
            if not 0 < args.settings_port < 65536:
                raise ConfigError("'--settings-port' must be a port from 1 to 65535")
            identity_key = identity_key_from(args.identity_key_file)
            if identity_key is None:
                raise ConfigError("'--settings-port' needs the identity key of HYDRA-UMC-SERVER (--identity-key-file): the route is never open")
        bridge = build_bridge(config)
    except ValueError as error:  # ConfigError is a ValueError
        print(f"hydra-umc-bridge-ros2: configuration error: {error}", file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"hydra-umc-bridge-ros2: {error}", file=sys.stderr)
        return 3
    if identity_key is not None:
        try:
            start_settings_server(
                args.settings_host,
                args.settings_port,
                Sros2Settings(args.config, config.security.require_sros2, args.allow_settings_change, os.environ, sros2_problems, load_service_config),
                identity_key,
            )
        except OSError as error:
            print(f"hydra-umc-bridge-ros2: cannot open the settings route: {error}", file=sys.stderr)
            return 2
    mqtt = config.mqtt
    # Restart=on-failure: a non-zero exit when the loop ends brings the service back
    try:
        run_forever(
            bridge,
            mqtt.host,
            mqtt.port,
            mqtt.client_id,
            max_connect_attempts=mqtt.max_connect_attempts,
            connect_retry_delay_seconds=mqtt.connect_retry_delay_seconds,
            username=mqtt.username or None,
            password=password,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("the bridge stopped")
        return 1
    return 1


__all__ = [
    "ConfigError",
    "MqttConfig",
    "NodeConfig",
    "SecurityConfig",
    "ServiceConfig",
    "apply_ros_parameters",
    "build_bridge",
    "check_security",
    "load_service_config",
    "main",
    "sros2_problems",
]
