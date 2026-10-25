"""Typed, validated view of ``config.yaml``.

The raw config is a nested dict whose values usually arrive as strings from
environment variables. It is parsed once at start-up so that a bad value fails
immediately with a clear message, not halfway through a batch.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from outward.errors import ConfigError

# Oracle identifier: letter first, then letters, digits, _, $ or #.
_ORACLE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


def qualified_procedure(package: str, procedure: str) -> str:
    """Return ``package.procedure`` after validating every part.

    oracledb interpolates the procedure name into an anonymous PL/SQL block,
    so it must never contain anything but identifiers. This matters because
    some procedure names (INTEGRATION_NAME) come from the database at runtime.
    """
    parts = package.split(".") + [procedure]
    for part in parts:
        if not _ORACLE_IDENTIFIER.match(part or ""):
            raise ValueError(f"Invalid Oracle identifier {part!r} in {package}.{procedure}")
    return ".".join(parts)


@dataclass(frozen=True)
class Procedures:
    package: str
    get_pending_tasks: str
    complete_task: str
    container_startup: str

    def qualified(self, procedure: str) -> str:
        return qualified_procedure(self.package, procedure)


@dataclass(frozen=True)
class Settings:
    container_id: str
    institution_number: str
    mount_checks: tuple[Path, ...]
    archive_root: Path
    retry_limit: int
    chunks: int
    array_size: int
    procedures: Procedures
    clob_encoding: str = "cp1252"
    successor_timeout: float | None = None

    @classmethod
    def from_dict(cls, cfg: Mapping[str, Any]) -> "Settings":
        try:
            app = cfg["app"]
            bankworks = cfg["bankworks"]
            procs = bankworks["procs"]
            settings = cls(
                container_id=_required_str(app, "container_id"),
                institution_number=_required_str(bankworks, "institution_number"),
                mount_checks=_path_list(app["paths"].get("mount_checks")),
                archive_root=Path(_required_str(app["paths"], "archive")),
                retry_limit=_int(bankworks, "retry_limit", allow_zero=True),
                chunks=_int(bankworks, "chunks"),
                array_size=_int(bankworks, "array_size"),
                procedures=Procedures(
                    package=_required_str(procs, "package"),
                    get_pending_tasks=_required_str(procs, "get_pending_tasks"),
                    complete_task=_required_str(procs, "complete_task"),
                    container_startup=_required_str(procs, "container_startup"),
                ),
                clob_encoding=str(app.get("clob_encoding") or "cp1252"),
                successor_timeout=_optional_timeout(app.get("successor_timeout")),
            )
        except KeyError as e:
            raise ConfigError(f"Missing configuration key {e}") from e

        # Validate the static procedure names now rather than on first use.
        p = settings.procedures
        try:
            for name in (p.get_pending_tasks, p.complete_task, p.container_startup):
                p.qualified(name)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        return settings


def _required_str(section: Mapping[str, Any], key: str) -> str:
    value = section[key]
    if value is None or str(value).strip() == "":
        raise ConfigError(f"Configuration value {key!r} is empty")
    return str(value).strip()


def _int(section: Mapping[str, Any], key: str, allow_zero: bool = False) -> int:
    try:
        value = int(section[key])
    except (TypeError, ValueError) as e:
        raise ConfigError(
            f"Configuration value {key!r} must be an integer, got {section[key]!r}"
        ) from e
    if value < 0 or (value == 0 and not allow_zero):
        raise ConfigError(f"Configuration value {key!r} must be positive, got {value}")
    return value


def _path_list(value: Any) -> tuple[Path, ...]:
    paths = tuple(Path(p.strip()) for p in str(value or "").split(",") if p.strip())
    if not paths:
        raise ConfigError("Configuration value 'mount_checks' must list at least one file")
    return paths


def _optional_timeout(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(
            f"Configuration value 'successor_timeout' must be a number, got {value!r}"
        ) from e
    return seconds if seconds > 0 else None
