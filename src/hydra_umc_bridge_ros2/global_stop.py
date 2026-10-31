# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Obey the global stop of the server
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The announcement of the global stop, as this bridge reads it.

When the server stops every robot it can announce it on `<mqtt prefix>/safety/estop` (a retained `SafetyState` message: `SAFE_STOP`, later `READY`). This module
keeps the last state the bridge heard of that topic and says whether a command that moves something may run.

Off by default: with the switch off the bridge does not even subscribe to the topic and behaves exactly as before. With the switch on:

* while the last message says `SAFE_STOP` (or says anything else than `READY`, or cannot be read), every command that moves something is refused with a reason;
  stops and read-only requests keep working, and the refusal is an answer, never silence;
* only a message that says `READY` ends it;
* a bridge that never heard the topic is left as it was, unless `strict` is also on: then it refuses until the first message arrives (a bridge that must not move
  before it knows the state of the installation).

The state lives in this process: it is read again from the broker (the message is retained) after every reconnect.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

DEFAULT_TOPIC = "hydra/swarm/safety/estop"
STATE_UNKNOWN = "UNKNOWN"
STATE_READY = "READY"
STATE_SAFE_STOP = "SAFE_STOP"
STATE_UNREADABLE = "UNREADABLE"

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GlobalStopSettings:
    """`enabled` is the switch (off by default); `topic` is where the server announces; `strict` also refuses before the first message."""

    enabled: bool = False
    topic: str = DEFAULT_TOPIC
    strict: bool = False

    def __post_init__(self) -> None:
        topic = self.topic
        if not isinstance(topic, str) or not topic or topic.startswith("/") or topic.endswith("/") or "+" in topic or "#" in topic or "\x00" in topic:
            raise ValueError("the global stop topic must be a plain MQTT topic without wildcards")


def settings_from_environment(name: str) -> GlobalStopSettings:
    """`HYDRA_UMC_<name>_GLOBAL_STOP` (1 switches it on), `..._GLOBAL_STOP_TOPIC` and `..._GLOBAL_STOP_STRICT` (1 also refuses before the first message)."""
    base = f"HYDRA_UMC_{name}_GLOBAL_STOP"
    enabled = os.environ.get(base, "").strip().lower() in _TRUE
    topic = os.environ.get(f"{base}_TOPIC", "").strip() or DEFAULT_TOPIC
    strict = os.environ.get(f"{base}_STRICT", "").strip().lower() in _TRUE
    return GlobalStopSettings(enabled, topic, strict)


class GlobalStopGuard:
    """Thread safe: the network thread feeds `observe()`, the command lanes ask `blocks()`."""

    def __init__(self, settings: GlobalStopSettings | None = None) -> None:
        self.settings = settings if settings is not None else GlobalStopSettings()
        self._lock = threading.Lock()
        self._state = STATE_UNKNOWN

    @classmethod
    def from_environment(cls, name: str) -> "GlobalStopGuard":
        return cls(settings_from_environment(name))

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def topic(self) -> str:
        return self.settings.topic

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def observe(self, payload: bytes) -> str:
        """One message of the announcement topic; returns the state it leaves the guard in. Anything but a clear `READY` is a stop."""
        state = STATE_UNREADABLE
        try:
            body = json.loads(payload)
        except (ValueError, RecursionError):
            body = None
        if isinstance(body, dict):
            declared = body.get("state")
            if declared == STATE_READY:
                state = STATE_READY
            elif declared == STATE_SAFE_STOP:
                state = STATE_SAFE_STOP
        with self._lock:
            self._state = state
        return state

    def blocks(self) -> bool:
        """True when a command that moves something has to be refused now."""
        if not self.settings.enabled:
            return False
        with self._lock:
            state = self._state
        if state == STATE_READY:
            return False
        if state == STATE_UNKNOWN:
            return self.settings.strict
        return True

    def reason(self) -> str:
        state = self.state
        if state == STATE_UNKNOWN:
            return "the global stop state is not known yet - nothing moves until the server announces READY"
        if state == STATE_UNREADABLE:
            return "the global stop announcement could not be read - nothing moves until the server announces READY"
        return "global stop active (SAFE_STOP) - nothing moves until the server announces READY"

    def blocks_job(self, payload: bytes) -> bool:
        """A job is refused while stopped, except an abort: it only ever stops things."""
        if not self.blocks():
            return False
        try:
            body = json.loads(payload)
        except (ValueError, RecursionError):
            return True
        phase = body.get("phase") if isinstance(body, dict) else None
        return not (isinstance(phase, str) and phase.strip().upper() == "ABORT")

    def blocks_assertion(self, payload: bytes, key: str) -> bool:
        """For a message that asserts or withdraws a line (`key` true/false): withdrawing is always allowed, asserting is refused while stopped."""
        if not self.blocks():
            return False
        try:
            body = json.loads(payload)
        except (ValueError, RecursionError):
            return True
        return not (isinstance(body, dict) and body.get(key) is False)

    def refusal(
        self,
        suffix: str,
        payload: bytes,
        *,
        moving: frozenset[str] = frozenset(),
        jobs: frozenset[str] = frozenset({"cmd/job"}),
        assertions: dict[str, str] | None = None,
    ) -> str | None:
        """The reason to refuse the command `suffix` (the topic below the prefix) now, or None when it may run: `moving` always refused while stopped, `jobs` unless they abort, `assertions` unless they withdraw."""
        if not self.settings.enabled:
            return None
        if suffix in moving and self.blocks():
            return self.reason()
        if suffix in jobs and self.blocks_job(payload):
            return self.reason()
        if assertions and suffix in assertions and self.blocks_assertion(payload, assertions[suffix]):
            return self.reason()
        return None


def follow_on_connect(guard: GlobalStopGuard):
    """The `on_connected` callback for `open_bridge_client`: subscribes to the announcement (QoS 1) after every (re)connect, so the retained state arrives at once; None when the switch is off."""
    if not guard.enabled:
        return None

    def on_connected(client: object) -> None:
        client.subscribe(guard.topic, qos=1)  # type: ignore[attr-defined]

    return on_connected
