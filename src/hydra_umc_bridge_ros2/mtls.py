# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Mutual TLS: the bridge proves itself with a client certificate
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The client-certificate side of the link between the bridge and the HYDRA-UMC server (its MQTT broker).

Off unless `HYDRA_UMC_ROS2_MTLS=1`. When it is on, three paths are required and nothing else is read:

* `HYDRA_UMC_ROS2_MTLS_CA`: the CA that signed the server certificate. Only this CA is trusted (the system store is not), and the host name of the server is checked.
* `HYDRA_UMC_ROS2_MTLS_CERT`: the certificate the bridge shows. Its common name is the identity the broker may bind to the client ID.
* `HYDRA_UMC_ROS2_MTLS_KEY`: the private key, given by path only. It is refused when other users can read it (it must be mode 0600 or stricter; the check is not made on Windows,
  which has no such mode) and when it lies inside a git working tree.

The connection is TLS 1.2 or newer with the server certificate required and verified. Turning it on while the plain `HYDRA_MQTT_TLS*` settings of the SDK are also given is an error:
two ways to set one link would leave it to the order in which they run. No key or certificate is ever kept in this repository or printed.
"""

from __future__ import annotations

import os
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

ENV_SWITCH = "HYDRA_UMC_ROS2_MTLS"
ENV_CA = "HYDRA_UMC_ROS2_MTLS_CA"
ENV_CERT = "HYDRA_UMC_ROS2_MTLS_CERT"
ENV_KEY = "HYDRA_UMC_ROS2_MTLS_KEY"
SDK_TLS_NAMES = ("HYDRA_MQTT_TLS", "HYDRA_MQTT_TLS_CA", "HYDRA_MQTT_TLS_CERT", "HYDRA_MQTT_TLS_KEY")
_TRUE = {"1", "true", "yes", "on"}


class MutualTlsError(ValueError):
    """The mutual TLS settings are incomplete, unsafe or do not match the files; nothing was connected."""


@dataclass(frozen=True)
class MutualTlsSettings:
    enabled: bool = False
    ca_path: str = ""
    cert_path: str = ""
    key_path: str = ""


def settings_from_environment(env: Optional[Mapping[str, str]] = None) -> MutualTlsSettings:
    """The settings of the environment; off (and empty) when the switch is not a real yes."""
    source = os.environ if env is None else env
    if (source.get(ENV_SWITCH) or "").strip().lower() not in _TRUE:
        return MutualTlsSettings()
    return MutualTlsSettings(
        True,
        (source.get(ENV_CA) or "").strip(),
        (source.get(ENV_CERT) or "").strip(),
        (source.get(ENV_KEY) or "").strip(),
    )


def _inside_git_tree(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in path.resolve().parents)


def check_files(settings: MutualTlsSettings) -> None:
    """Refuse a settings object that is on but incomplete, whose files are missing, or whose key is exposed."""
    if not settings.enabled:
        return
    for name, value in ((ENV_CA, settings.ca_path), (ENV_CERT, settings.cert_path), (ENV_KEY, settings.key_path)):
        if not value:
            raise MutualTlsError(f"{name} is needed when {ENV_SWITCH} is on")
        if not Path(value).is_absolute():
            raise MutualTlsError(f"{name} must be an absolute path")
        if not Path(value).is_file():
            raise MutualTlsError(f"{name} does not name a readable file")
    key = Path(settings.key_path)
    if _inside_git_tree(key):
        raise MutualTlsError(f"{ENV_KEY} lies inside a git working tree: keep the private key outside any repository")
    if os.name != "nt" and stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise MutualTlsError(f"{ENV_KEY} can be read by other users: set its mode to 0600")


def check_no_conflict(env: Optional[Mapping[str, str]] = None) -> None:
    source = os.environ if env is None else env
    given = [name for name in SDK_TLS_NAMES if (source.get(name) or "").strip()]
    if given:
        raise MutualTlsError(f"{ENV_SWITCH} is on and so is {', '.join(given)}: use one or the other")


def build_context(settings: MutualTlsSettings) -> ssl.SSLContext:
    """The client context: TLS 1.2+, the server certificate required and checked against the configured CA only, and the client certificate loaded."""
    check_files(settings)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    try:
        context.load_verify_locations(cafile=settings.ca_path)
        context.load_cert_chain(certfile=settings.cert_path, keyfile=settings.key_path)
    except (ssl.SSLError, OSError) as error:
        raise MutualTlsError(f"the certificate files could not be loaded: {type(error).__name__}") from error
    return context


class _MqttWithContext:
    """Stands in for `paho.mqtt.client` so that every client it makes carries the context before it connects."""

    def __init__(self, real: object, context: ssl.SSLContext) -> None:
        self._real = real
        self._context = context
        self.CallbackAPIVersion = real.CallbackAPIVersion  # type: ignore[attr-defined]

    def Client(self, *args: object, **kwargs: object) -> object:  # noqa: N802 - the name paho uses
        client = self._real.Client(*args, **kwargs)  # type: ignore[attr-defined]
        client.tls_set_context(self._context)  # type: ignore[attr-defined]
        return client


def mqtt_module_for(settings: MutualTlsSettings, env: Optional[Mapping[str, str]] = None, real: object = None) -> Optional[object]:
    """The `mqtt_module` to hand to `open_bridge_client`, or None when mutual TLS is off (nothing changes then)."""
    if not settings.enabled:
        return None
    check_no_conflict(env)
    context = build_context(settings)
    if real is None:
        try:
            import paho.mqtt.client as real  # type: ignore[import-untyped,no-redef]
        except ImportError as error:
            raise RuntimeError("paho-mqtt is not installed - install it to connect to a real HYDRA-UMC-MQTT-BROKER") from error
    return _MqttWithContext(real, context)
