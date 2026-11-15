# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - src/hydra_umc_bridge_ros2/settings_http.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The route by which HYDRA-UMC-SERVER reads and changes the requirement of ROS 2 security of this bridge.

The requirement (`security.require_sros2`, off by default) lives in the configuration file of the service and is checked once, when the service starts. This
module adds a small HTTP listener, closed unless the operator opens it, with two routes:

* `GET /settings/sros2` (any role of the SERVER): `requireSros2` is the value now in the configuration file, `active` the value this running service started with,
  `restartNeeded` says they differ, `canChange` whether the service was started allowing the change, and `environmentReady` whether the three ROS 2 security
  variables are set in the environment of this process (a restart with the requirement on would then pass the check).
* `POST /settings/sros2` (administrator only): the body is `{"requireSros2": true|false, "confirm": true}`; `confirm` is needed in both directions. The value is
  written to the configuration file and applies when the service restarts: nothing about the running service changes, and the answer says so
  (`appliesAfterRestart`). Without `--allow-settings-change` the answer is 403 and the file is not touched.

Nothing is open: the listener is not started without `--settings-port` and the identity key of the SERVER (`--identity-key-file`, `server_identity.py`), it
listens on loopback unless told otherwise, and every request needs a valid signed token of the SERVER. The standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .server_identity import accepts

SETTINGS_PATH = "/settings/sros2"
MAX_BODY_BYTES = 4096


class SettingsFileError(ValueError):
    """The configuration file cannot take the change (it is not a JSON object, or the result would not load)."""


def read_required(path: str | Path | None) -> bool:
    """The value of `security.require_sros2` in the file; false when there is no file or no such key (the default)."""
    if path is None or not Path(path).is_file():
        return False
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    security = raw.get("security") if isinstance(raw, dict) else None
    value = security.get("require_sros2") if isinstance(security, dict) else None
    return value is True


def write_required(path: str | Path, wanted: bool, check: Callable[[Path], object]) -> None:
    """Set `security.require_sros2` in the file, keeping every other key, and replace the file only when the result still loads (`check` raises otherwise)."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    except (OSError, ValueError) as error:
        raise SettingsFileError(f"cannot read {target.name}: {error}") from error
    if not isinstance(raw, dict):
        raise SettingsFileError("the configuration must be a JSON object")
    security = raw.get("security")
    if security is None:
        security = {}
    if not isinstance(security, dict):
        raise SettingsFileError("'security' must be a JSON object")
    raw["security"] = dict(security, require_sros2=wanted)
    handle, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(raw, stream, indent=2)
            stream.write("\n")
        if target.is_file():
            shutil.copymode(target, temporary)
        try:
            check(Path(temporary))
        except ValueError as error:
            raise SettingsFileError(str(error)) from error
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class Sros2Settings:
    """What the listener needs from the service: where the file is, what the service started with and whether it may change the file."""

    config_path: str | None
    active: bool
    allow_change: bool
    environment: Mapping[str, str]
    problems: Callable[[Mapping[str, str]], list[str]]
    check: Callable[[Path], object]

    def view(self) -> dict[str, Any]:
        configured = read_required(self.config_path)
        return {
            "requireSros2": configured,
            "active": self.active,
            "restartNeeded": configured != self.active,
            "canChange": bool(self.allow_change and self.config_path),
            "environmentReady": not self.problems(self.environment),
        }


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler: BaseHTTPRequestHandler, status: int, message: str, code: str | None = None) -> None:
    _write_json(handler, status, dict({"error": message}, **({"code": code} if code else {})))


class SettingsHandler(BaseHTTPRequestHandler):
    timeout = 15.0
    server: SettingsServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        if not accepts(self, "", self.server.identity_key):
            _error(self, 401, "a valid identity is required")
        elif urlparse(self.path).path == SETTINGS_PATH:
            _write_json(self, 200, self.server.settings.view())
        else:
            _error(self, 404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not accepts(self, "", self.server.identity_key):
            _error(self, 401, "a valid identity is required")
            return
        if urlparse(self.path).path != SETTINGS_PATH:
            _error(self, 404, "not found")
            return
        identity = getattr(self, "identity", None)
        if identity is None or identity.role != "admin":
            _error(self, 403, "the requirement can be changed only with the signed token of an administrator of HYDRA-UMC-SERVER")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("the body must be a small JSON object")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("the body must be a JSON object")
        except (ValueError, UnicodeDecodeError) as error:
            _error(self, 400, f"malformed JSON body: {error}")
            return
        if set(body) - {"requireSros2", "confirm"} or not isinstance(body.get("requireSros2"), bool):
            _error(self, 400, "the body must be an object with requireSros2 (true or false) and confirm: true")
            return
        if body.get("confirm") is not True:
            _error(self, 400, "changing the requirement needs confirm: true", "CONFIRMATION_REQUIRED")
            return
        settings = self.server.settings
        if not (settings.allow_change and settings.config_path):
            _error(self, 403, "this bridge was started without allowing the change (--allow-settings-change)", "CHANGE_NOT_ALLOWED")
            return
        wanted = body["requireSros2"]
        before = read_required(settings.config_path)
        if wanted != before:
            try:
                write_required(settings.config_path, wanted, settings.check)
            except SettingsFileError as error:
                _error(self, 409, str(error), "SETTING_REFUSED")
                return
            print(f"[bridge-ros2] {'notice' if wanted else 'WARNING'}: security.require_sros2 was set to {str(wanted).lower()} in the configuration file through the settings route by {identity.username}; it applies when the service restarts", file=sys.stderr)
        _write_json(self, 200, dict(settings.view(), changed=wanted != before, appliesAfterRestart=True))


class SettingsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: Sros2Settings, identity_key: bytes) -> None:
        super().__init__(address, SettingsHandler)
        self.settings = settings
        self.identity_key = identity_key


def start_settings_server(host: str, port: int, settings: Sros2Settings, identity_key: bytes) -> SettingsServer:
    """Listen in a thread of its own; the caller keeps the server to shut it down. Raises OSError when the port cannot be bound."""
    server = SettingsServer((host, port), settings, identity_key)
    threading.Thread(target=server.serve_forever, name="bridge-ros2-settings", daemon=True).start()
    return server
