# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Extension: the /diagnostics topic of ROS 2 towards HYDRA-UMC (read only)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Read the standard `/diagnostics` topic of ROS 2 and publish a bounded summary on `hydra/bridges/ros2/diagnostics` (retained).

Read only by construction: the extension subscribes to one ROS 2 topic and publishes one MQTT topic. It claims no command topic, creates no ROS publisher, service or action
client, and what it reads never reaches the gate of the jobs. Off by default (`extensions.diagnostics.enabled`, in the file of the service).

`diagnostic_msgs/DiagnosticArray` is the message every ROS 2 driver and the `diagnostic_aggregator` publish: a list of statuses with a `level` (OK, WARN, ERROR, STALE), a `name`,
a `message`, a `hardware_id` and key/value pairs. The summary keeps the last status of every name (at most `max_statuses`; a new name over the limit is counted in
`dropped_statuses`, never kept), the worst level as `health`, and a count per level. Every text is cleaned of control characters and cut at `max_text_chars`. The key/value pairs
are left out unless `include_values` is on, and the values of keys that look like a secret are replaced even then. When nothing was heard for `stale_after_s` the `health` is `STALE`:
a silent driver is never reported as healthy. A level that is not one of the four is read as `ERROR`.

The summary is published at most once per `publish_interval_s` and always carries `observed_at` (milliseconds since the epoch), so a reader sees how old it is.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .extensions import BridgeExtension, ExtensionConfigError, Publish, section_from_dict

_LOG = logging.getLogger(__name__)

NAME = "diagnostics"
DIAGNOSTICS_TOPIC = "hydra/bridges/ros2/diagnostics"
LEVELS = ("OK", "WARN", "ERROR", "STALE")
MAX_SUMMARY_CHARS = 100_000
_ROS_TOPIC = re.compile(r"(/[A-Za-z0-9_]+)+")
_SECRET_KEY = re.compile(r"pass|secret|token|key|credential|auth", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class DiagnosticsSettings:
    enabled: bool = False
    # the ROS 2 topic that is read
    topic: str = "/diagnostics"
    publish_interval_s: float = 5.0
    # nothing heard for this long: the health is STALE
    stale_after_s: float = 30.0
    max_statuses: int = 64
    max_values: int = 16
    max_text_chars: int = 200
    # the key/value pairs of every status; off, because they can hold anything a driver chose to print
    include_values: bool = False
    # when not empty, only the statuses whose name starts with one of these are kept
    name_prefixes: tuple[str, ...] = ()


def parse(section: object) -> DiagnosticsSettings:
    settings = section_from_dict(section, DiagnosticsSettings, NAME)
    if not _ROS_TOPIC.fullmatch(settings.topic):
        raise ExtensionConfigError("'extensions.diagnostics.topic' must be a ROS 2 topic name such as /diagnostics")
    for key in ("publish_interval_s", "stale_after_s"):
        value = getattr(settings, key)
        if not math.isfinite(value) or value <= 0:
            raise ExtensionConfigError(f"'extensions.diagnostics.{key}' must be a number of seconds above 0")
    for key, top in (("max_statuses", 1000), ("max_values", 100), ("max_text_chars", 2000)):
        value = getattr(settings, key)
        if not 1 <= value <= top:
            raise ExtensionConfigError(f"'extensions.diagnostics.{key}' must be between 1 and {top}")
    if any(not prefix for prefix in settings.name_prefixes):
        raise ExtensionConfigError("'extensions.diagnostics.name_prefixes' must not hold an empty text")
    return settings


def level_name(raw: object) -> str:
    """The name of a DiagnosticStatus level: rclpy gives one byte, other code an int. Anything else is read as ERROR."""
    value: object = raw
    if isinstance(raw, (bytes, bytearray)):
        value = raw[0] if len(raw) == 1 else None
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(LEVELS):
        return LEVELS[value]
    return "ERROR"


class DiagnosticsAggregator:
    """The last status of every name, kept within the limits of the settings. Pure: it needs no ROS 2."""

    def __init__(self, settings: DiagnosticsSettings, clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self._settings = settings
        self._clock = clock
        self._wall = wall
        self._started = clock()
        self._last_heard: float | None = None
        self._statuses: dict[str, dict[str, object]] = {}
        self.dropped_statuses = 0

    def _clean(self, value: object) -> str:
        return _CONTROL.sub(" ", str(value))[: self._settings.max_text_chars]

    def _values(self, raw: object) -> list[dict[str, str]]:
        pairs: list[dict[str, str]] = []
        for item in list(raw or [])[: self._settings.max_values]:  # type: ignore[call-overload]
            key = self._clean(getattr(item, "key", ""))
            value = "***" if _SECRET_KEY.search(key) else self._clean(getattr(item, "value", ""))
            pairs.append({"key": key, "value": value})
        return pairs

    def ingest(self, array: Any) -> int:
        """One DiagnosticArray (anything with `.status`); returns how many statuses were kept. A malformed message keeps nothing and raises nothing."""
        self._last_heard = self._clock()
        kept = 0
        try:
            statuses = list(getattr(array, "status", None) or [])
        except TypeError:
            return 0
        for status in statuses:
            name = self._clean(getattr(status, "name", ""))
            prefixes = self._settings.name_prefixes
            if not name or (prefixes and not name.startswith(prefixes)):
                continue
            if name not in self._statuses and len(self._statuses) >= self._settings.max_statuses:
                self.dropped_statuses += 1
                continue
            record: dict[str, object] = {
                "name": name,
                "level": level_name(getattr(status, "level", None)),
                "message": self._clean(getattr(status, "message", "")),
                "hardware_id": self._clean(getattr(status, "hardware_id", "")),
            }
            if self._settings.include_values:
                record["values"] = self._values(getattr(status, "values", None))
            self._statuses[name] = record
            kept += 1
        return kept

    def snapshot(self) -> dict[str, object] | None:
        """The summary to publish, or None while nothing was heard and the first silence has not lasted `stale_after_s` yet."""
        now = self._clock()
        silent_since = self._last_heard if self._last_heard is not None else self._started
        silent = now - silent_since > self._settings.stale_after_s
        if self._last_heard is None and not silent:
            return None
        records = [self._statuses[name] for name in sorted(self._statuses)]
        counts = {level: sum(1 for record in records if record["level"] == level) for level in LEVELS}
        health = "STALE" if silent else max((str(record["level"]) for record in records), key=LEVELS.index, default="OK")
        summary: dict[str, object] = {
            "observed_at": int(self._wall() * 1000),
            "health": health,
            "counts": counts,
            "dropped_statuses": self.dropped_statuses,
            "statuses": records,
        }
        if silent:
            summary["reason"] = f"nothing heard on the diagnostics topic for more than {self._settings.stale_after_s:g} s"
        return summary


def summary_text(summary: dict[str, object]) -> str:
    """The summary as JSON, within `MAX_SUMMARY_CHARS`: the key/value pairs go first, then the statuses of the end of the list."""
    text = json.dumps(summary, allow_nan=False, sort_keys=True)
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    records = [dict(record) for record in summary["statuses"]]  # type: ignore[attr-defined]
    for record in records:
        record.pop("values", None)
    reduced = dict(summary, statuses=records, shortened=True)
    text = json.dumps(reduced, allow_nan=False, sort_keys=True)
    while len(text) > MAX_SUMMARY_CHARS and records:
        del records[len(records) // 2 :]
        text = json.dumps(dict(reduced, statuses=records), allow_nan=False, sort_keys=True)
    return text


def ros_subscribe(node: Any, topic: str, callback: Callable[[Any], None]) -> object:
    """The one place `diagnostic_msgs` is imported (lazily): a plain subscription to the topic, reliable and volatile like the publishers of the drivers."""
    try:
        from diagnostic_msgs.msg import DiagnosticArray  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(f"diagnostic_msgs is not installed - the diagnostics extension cannot read {topic}: {error}") from error
    return node.create_subscription(DiagnosticArray, topic, callback, 10)


class DiagnosticsExtension(BridgeExtension):
    name = NAME

    def __init__(
        self,
        settings: DiagnosticsSettings,
        *,
        subscribe: Callable[[Any, str, Callable[[Any], None]], object] = ros_subscribe,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        run_thread: bool = True,
    ) -> None:
        self.settings = settings
        self.aggregator = DiagnosticsAggregator(settings, clock, wall)
        self._subscribe = subscribe
        self._run_thread = run_thread
        self._lock = threading.Lock()
        self._publish: Publish | None = None
        self._node: Any = None
        self._subscription: object = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def on_array(self, array: Any) -> None:
        with self._lock:
            self.aggregator.ingest(array)

    def tick(self) -> bool:
        """Publish the summary now (the thread calls this once per interval). Never raises."""
        publish = self._publish
        if publish is None:
            return False
        try:
            with self._lock:
                summary = self.aggregator.snapshot()
            if summary is None:
                return False
            publish(DIAGNOSTICS_TOPIC, summary_text(summary), True)
            return True
        except Exception:  # noqa: BLE001 - a failed publication is tried again at the next tick
            _LOG.exception("could not publish the diagnostics summary")
            return False

    def _loop(self) -> None:
        while not self._stop.wait(self.settings.publish_interval_s):
            self.tick()

    def start(self, node: Any, publish: Publish) -> None:
        self._publish = publish
        self._node = node
        self._subscription = self._subscribe(node, self.settings.topic, self.on_array)
        self._stop.clear()
        if self._run_thread:
            self._thread = threading.Thread(target=self._loop, name="hydra-ros2-diagnostics", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None
        if self._subscription is not None and self._node is not None:
            try:
                self._node.destroy_subscription(self._subscription)
            except Exception:  # noqa: BLE001 - the node may already be gone
                _LOG.debug("the diagnostics subscription was already gone", exc_info=True)
        self._subscription = None
        self._publish = None


def build(settings: DiagnosticsSettings, bridge: Any) -> DiagnosticsExtension | None:
    return DiagnosticsExtension(settings) if settings.enabled else None
