#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Standalone build version synchronizer
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Synchronize pyproject, manifest and CHANGELOG after a successful build.

The utility is intentionally local to this repository so its normal build
remains reproducible from a standalone checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "hydra-umc.project.json"
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r'(?m)^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$')


def successor(version: str) -> str:
    """Return the documented decimal odometer successor (0.0.9 -> 0.1.0)."""
    major, minor, patch = (int(part) for part in version.split("."))
    patch += 1
    if patch == 10:
        minor, patch = minor + 1, 0
    if minor == 10:
        major, minor = major + 1, 0
    return f"{major}.{minor}.{patch}"


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = manifest.get("version")
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    match = VERSION_RE.search(pyproject)
    if not isinstance(declared, str) or match is None:
        raise SystemExit("ERROR: manifest or pyproject native version is invalid")
    native = ".".join(match.group(index) for index in (1, 2, 3))
    if native != declared:
        raise SystemExit(
            f"ERROR: native version {native} differs from manifest {declared}; "
            "repair the mismatch before building"
        )

    updated = successor(native)
    pyproject = pyproject[: match.start(1)] + updated.split(".")[0] + pyproject[match.end(1) :]
    # Re-read the match after replacing its first component so every component
    # is replaced from a current span, not a stale offset.
    match = VERSION_RE.search(pyproject)
    if match is None:
        raise SystemExit("ERROR: could not rewrite native version")
    pyproject = pyproject[: match.start()] + f'version = "{updated}"' + pyproject[match.end() :]
    manifest["version"] = updated

    changelog = CHANGELOG.read_text(encoding="utf-8")
    entry = (
        f"## [{updated}]\n\n"
        "- Successful incremental build: synchronized package metadata and "
        "`hydra-umc.project.json`.\n\n"
    )
    first_release = re.search(r"(?m)^## \[\d+\.\d+\.\d+\]", changelog)
    if first_release is None:
        raise SystemExit("ERROR: CHANGELOG.md has no release heading")

    PYPROJECT.write_text(pyproject, encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    CHANGELOG.write_text(changelog[: first_release.start()] + entry + changelog[first_release.start() :], encoding="utf-8")
    print(f"HYDRA-UMC-BRIDGE-ROS2 version: v{native} -> v{updated}")
    return 0


def _delegate_to_shared_utility():
    """Hand the bump over to the shared utility once the version has four parts.

    Returns its exit code, or None when this step is an ordinary three-part one.
    """
    import importlib.util
    import json as _json
    from pathlib import Path as _Path

    here = _Path(__file__).resolve().parent
    root = here if (here / "bump_manifest_version.py").is_file() else here.parent
    utility, manifest = root / "bump_manifest_version.py", root / "hydra-umc.project.json"
    if not utility.is_file() or not manifest.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_shared_bump", utility)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = _json.loads(manifest.read_text(encoding="utf-8")).get("version", "")
    if not hasattr(module, "FOUR_PART_FROM") or module.next_version(version).count(".") != 3:
        return None
    return module.main()


if __name__ == "__main__":
    _shared_exit = _delegate_to_shared_utility()
    if _shared_exit is not None:
        raise SystemExit(_shared_exit)


if __name__ == "__main__":
    raise SystemExit(main())
