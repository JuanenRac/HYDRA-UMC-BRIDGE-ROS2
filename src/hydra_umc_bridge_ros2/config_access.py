# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - src/hydra_umc_bridge_ros2/config_access.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Where the configuration file lives and whether the account of the service can read it.

The service runs as its own account. The shared directory ``/etc/hydra-umc`` can only be entered by the group of the agent (mode 0750), so a configuration
file kept there cannot be opened by this account: the service would not start, or would run without the file. The directory the unit names is therefore one
that systemd creates for this service alone (``ConfigurationDirectory=``), and the shared directory is never touched. Four small helpers live here:

* ``diagnose_access`` names the first directory the account cannot enter or the file it cannot read, for the message of a refusal;
* ``migrate_legacy_config`` copies the file of the earlier place into the new directory once, never overwriting and never deleting the old one;
* ``ensure_directory`` makes the directory of the service enterable by its account (mode 0750, group of the service);
* ``check_access`` is the check an installer runs as the service account before it stops the running service.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

DIRECTORY_NAME = "hydra-umc-bridge-ros2"
DEFAULT_CONFIG_PATH = "/etc/hydra-umc-bridge-ros2/bridge-ros2.json"
LEGACY_CONFIG_PATH = "/etc/hydra-umc/bridge-ros2.json"
DEFAULT_PARAMS_PATH = "/etc/hydra-umc-bridge-ros2/bridge-ros2-params.yaml"
LEGACY_PARAMS_PATH = "/etc/hydra-umc/bridge-ros2-params.yaml"
# the unit runs as the account hydra-umc: the files get its group when it exists
SERVICE_GROUP = "hydra-umc"


def permission_problem(path: str) -> str:
    """One sentence saying which directory the current account cannot enter or which file it cannot read, or "" when the permissions are not the reason
    (the file may simply not be there)."""
    target = Path(path)
    for directory in reversed([target, *target.parents][1:]):
        if not directory.exists():
            break  # a directory that is not there is a missing file, said below; it is not a permission
        try:
            entered = os.access(directory, os.X_OK)
        except OSError:
            entered = False
        if not entered:
            try:
                info = directory.stat()
                detail = f"mode {stat.filemode(info.st_mode)}, owner uid {info.st_uid}, group gid {info.st_gid}"
            except OSError:
                detail = "it cannot be inspected"
            return f"the directory {directory} cannot be entered by this account ({detail}); put the configuration file in a directory this account can read"
    if target.exists() and not os.access(target, os.R_OK):
        info = target.stat()
        return f"the file {target} cannot be read by this account (mode {stat.filemode(info.st_mode)}, owner uid {info.st_uid}, group gid {info.st_gid})"
    return ""


def diagnose_access(path: str) -> str:
    """One sentence saying why the current account cannot read ``path``, or "" when it can."""
    problem = permission_problem(path)
    if problem:
        return problem
    if not Path(path).exists():
        return f"the file {path} does not exist"
    return ""


def migrate_legacy_config(legacy: str, target: str, group: str = "") -> str:
    """Copy the configuration file from the old place to ``target`` once. Returns what was done, in a word: ``kept`` (target already there),
    ``none`` (nothing to migrate) or ``migrated``. Never overwrites, never deletes the old file."""
    destination = Path(target)
    if destination.exists():
        return "kept"
    source = Path(legacy)
    if not source.is_file():
        return "none"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o640)
    if group:
        shutil.chown(destination, user="root", group=group)
    return "migrated"


def ensure_directory(directory: str, group: str = "") -> str:
    """Make the directory of the service (and only that one) mode 0750, owned by root with the group of the service when it exists, so the account of
    the service can enter it whichever way systemd created it. Run as root by ``--prepare-config``. The shared ``/etc/hydra-umc`` is refused."""
    path = Path(directory)
    if path.name == "hydra-umc":
        raise ValueError(f"{directory} is the shared directory: it is never changed")
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o750)
    if group:
        shutil.chown(path, user="root", group=group)
    return "ready"


def group_exists(name: str) -> bool:
    try:
        import grp  # noqa: PLC0415 - POSIX only; the service itself runs on Linux

        grp.getgrnam(name)  # type: ignore[attr-defined,unused-ignore]
    except (ImportError, KeyError):
        return False
    return True


def check_access_all(paths: list[str]) -> tuple[int, str]:
    """(exit code, message) for several files: the first that cannot be read decides; 0 only when every one can."""
    messages = []
    for path in paths:
        code, message = check_access(path)
        if code:
            return code, message
        messages.append(message)
    return 0, "; ".join(messages)


def check_access(path: str) -> tuple[int, str]:
    """(exit code, message): 0 when this account can open and read ``path``, 2 with the reason when it cannot."""
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError as exc:
        reason = diagnose_access(path) or f"{type(exc).__name__}: {exc}"
        return 2, f"HYDRA-UMC-BRIDGE-ROS2: cannot read the configuration file {path}: {reason}"
    return 0, f"HYDRA-UMC-BRIDGE-ROS2: the configuration file {path} is readable by this account"
