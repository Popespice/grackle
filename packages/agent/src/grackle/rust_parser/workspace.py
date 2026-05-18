"""Cargo workspace reader.

Reads the workspace.members list from a root Cargo.toml (Python 3.11+
tomllib) and returns per-crate metadata. Never shells out to cargo —
resolution is purely filesystem-based.

If the root Cargo.toml has no [workspace] section, returns None (single-crate
project).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grackle.paths import to_posix

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class CrateInfo:
    """Metadata for one member crate in a Cargo workspace (or the lone crate)."""

    name: str
    root: Path  # absolute path to the crate directory
    posix_root: str  # POSIX path of root relative to the workspace/project root


def _read_crate_name(cargo_toml: Path) -> str:
    """Return the [package].name from a crate's Cargo.toml."""
    try:
        with cargo_toml.open("rb") as f:
            data = tomllib.load(f)
        return data.get("package", {}).get("name") or cargo_toml.parent.name
    except (OSError, tomllib.TOMLDecodeError):
        return cargo_toml.parent.name


def read_workspace(project_root: Path) -> list[CrateInfo] | None:
    """Read workspace members from the root Cargo.toml.

    Returns:
        A list of CrateInfo for each workspace member, or ``None`` if the
        root Cargo.toml has no ``[workspace]`` section (single-crate project).
        Returns an empty list (not None) if the [workspace] section exists but
        has no members.
    """
    cargo_toml = project_root / "Cargo.toml"
    if not cargo_toml.exists():
        return None

    try:
        with cargo_toml.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    if "workspace" not in data:
        return None

    member_globs: list[str] = data["workspace"].get("members", [])
    crates: list[CrateInfo] = []
    seen: set[Path] = set()

    for glob in member_globs:
        for member_dir in sorted(project_root.glob(glob)):
            if not member_dir.is_dir():
                continue
            member_cargo = member_dir / "Cargo.toml"
            if not member_cargo.exists():
                continue
            resolved = member_dir.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            crate_name = _read_crate_name(member_cargo)
            crates.append(
                CrateInfo(
                    name=crate_name,
                    root=member_dir,
                    posix_root=to_posix(member_dir, project_root),
                )
            )

    return crates


def get_crates(project_root: Path) -> list[CrateInfo]:
    """Return workspace member crates, or a single-crate list for non-workspace projects.

    Always returns at least one CrateInfo (the project root itself) so the
    resolver has a consistent data structure regardless of workspace layout.
    """
    workspace_crates = read_workspace(project_root)
    if workspace_crates is not None:
        return workspace_crates

    # Single-crate: treat the project root as the lone crate
    cargo_toml = project_root / "Cargo.toml"
    crate_name = _read_crate_name(cargo_toml) if cargo_toml.exists() else project_root.name
    return [CrateInfo(name=crate_name, root=project_root, posix_root="")]
