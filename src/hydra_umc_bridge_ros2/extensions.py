# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Optional extensions of the bridge
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Small optional modules that add one feed or one gated command to the bridge, all of them off by default.

An extension is a module `ext_<name>.py` of this package with two functions: `parse(section)` turns the object `extensions.<name>` of `docs/deploy/bridge.json` into
settings (a mistyped key or value is an error, never a silent default) and `build(settings, bridge)` returns the extension, or `None` when the settings say `enabled: false`.
The service reads the section, checks it at `--check`, builds the extensions once the bridge exists and `run_forever()` starts them after the node is spinning.

`BridgeExtension` is what the bridge needs of one:

* `subscriptions()` are extra MQTT filters, subscribed again after every reconnect;
* `claims(topic)` says which topics the extension answers; `inline(topic)` says that its handler is cheap and never blocks, so the network thread may run it at once
  (that is also the only way a retained message, such as the state of a robot, is delivered: the shared dispatcher drops retained messages for the command lane);
* `moving_verbs` and `job_verbs` are the command topics (below `hydra/bridges/ros2/`) that the global stop of the server refuses while it is active, like `cmd/job`;
* `start(node, publish)` and `stop()` run once, around the life of the node.
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import fields
from typing import Any, Callable, Mapping

from hydra_umc_sdk.bridge_transport import MqttPublish

_LOG = logging.getLogger(__name__)

Publish = Callable[[str, str, bool], None]
SECTION_NAME = re.compile(r"[a-z][a-z0-9_]{0,30}")


class ExtensionConfigError(ValueError):
    """The section of an extension cannot be used."""


class BridgeExtension:
    """The defaults of an extension: it subscribes to nothing, answers nothing and starts nothing."""

    name = "extension"
    moving_verbs: frozenset[str] = frozenset()
    job_verbs: frozenset[str] = frozenset()

    def subscriptions(self) -> list[tuple[str, int]]:
        return []

    def claims(self, topic: str) -> bool:
        return False

    def inline(self, topic: str) -> bool:
        return False

    def on_message(self, topic: str, payload: bytes, retained: bool) -> list[MqttPublish]:
        return []

    def start(self, node: Any, publish: Publish) -> None:
        return None

    def stop(self) -> None:
        return None


class ExtensionHost:
    """The extensions of one bridge."""

    def __init__(self) -> None:
        self._items: list[BridgeExtension] = []

    def add(self, extension: BridgeExtension) -> None:
        if any(item.name == extension.name for item in self._items):
            raise ValueError(f"the extension {extension.name} is already added")
        self._items.append(extension)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def names(self) -> list[str]:
        return [item.name for item in self._items]

    def subscriptions(self) -> list[tuple[str, int]]:
        return [pair for item in self._items for pair in item.subscriptions()]

    def claiming(self, topic: str) -> BridgeExtension | None:
        for item in self._items:
            if item.claims(topic):
                return item
        return None

    def moving_verbs(self) -> frozenset[str]:
        return frozenset(verb for item in self._items for verb in item.moving_verbs)

    def job_verbs(self) -> frozenset[str]:
        return frozenset(verb for item in self._items for verb in item.job_verbs)

    def start(self, node: Any, publish: Publish) -> None:
        """Start every extension; when one fails the ones already started are stopped and the error goes on (an enabled extension that cannot run is not skipped)."""
        started: list[BridgeExtension] = []
        try:
            for item in self._items:
                item.start(node, publish)
                started.append(item)
        except Exception:
            for item in reversed(started):
                self._stop_one(item)
            raise

    def stop(self) -> None:
        for item in reversed(self._items):
            self._stop_one(item)

    @staticmethod
    def _stop_one(item: BridgeExtension) -> None:
        try:
            item.stop()
        except Exception:  # noqa: BLE001 - stopping one must not keep the others running
            _LOG.exception("could not stop the extension %s", item.name)


def topic_matches(topic_filter: str, topic: str) -> bool:
    """MQTT topic filter matching (`+` one level, `#` the rest)."""
    filter_parts = topic_filter.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(filter_parts):
        if part == "#":
            return index == len(filter_parts) - 1
        if index >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[index]:
            return False
    return len(filter_parts) == len(topic_parts)


def section_from_dict(raw: object, kind: type, name: str):
    """A flat settings dataclass from a JSON object: unknown keys and values of the wrong type are errors. A tuple default takes a list of strings."""
    if raw is None:
        return kind()
    if not isinstance(raw, dict):
        raise ExtensionConfigError(f"'extensions.{name}' must be a JSON object")
    known = {item.name for item in fields(kind)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ExtensionConfigError(f"unknown keys in 'extensions.{name}': {', '.join(unknown)}")
    defaults = kind()
    values: dict[str, object] = {}
    for key, value in raw.items():
        wanted = type(getattr(defaults, key))
        label = f"'extensions.{name}.{key}'"
        if wanted is tuple:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ExtensionConfigError(f"{label} must be a list of strings")
            values[key] = tuple(value)
        elif wanted is float and isinstance(value, int) and not isinstance(value, bool):
            values[key] = float(value)
        elif not isinstance(value, wanted) or (wanted is int and isinstance(value, bool)):
            raise ExtensionConfigError(f"{label} must be a {wanted.__name__}")
        else:
            values[key] = value
    return kind(**values)


def load_module(name: str):
    """The module `ext_<name>` of this package; a name that is not a plain lowercase word, or a module that does not exist, is an error."""
    if not isinstance(name, str) or not SECTION_NAME.fullmatch(name):
        raise ExtensionConfigError(f"'{name}' is not the name of an extension")
    module_name = f"{__package__}.ext_{name}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            raise ExtensionConfigError(f"unknown extension '{name}'") from error
        raise


def parse_sections(raw: object) -> dict[str, object]:
    """The settings of every extension named in `extensions`; nothing named gives nothing."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ExtensionConfigError("'extensions' must be a JSON object")
    return {name: load_module(name).parse(section) for name, section in raw.items()}


def build_extensions(settings: Mapping[str, object], bridge: Any) -> None:
    """Build the enabled extensions and add them to the bridge."""
    for name, parsed in settings.items():
        extension = load_module(name).build(parsed, bridge)
        if extension is not None:
            bridge.extensions.add(extension)
