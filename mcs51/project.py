from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ProjectConfigError(ValueError):
    """Raised when a project configuration file is invalid."""


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    image: Path
    format: str | None = None
    origin: int | None = None
    entry: int | None = None
    max_instructions: int | None = None
    serial_input: str | None = None
    serial_input_hex: str | None = None
    runtime: Path | None = None
    symbols: Path | None = None
    breakpoints: tuple[str, ...] = ()
    watchpoints: tuple[str, ...] = ()
    watch_log: bool | None = None
    inspect: tuple[str, ...] = ()
    step_limit: int | None = None
    dump_direct: tuple[str, ...] = ()
    dump_xram: tuple[str, ...] = ()
    trace_file: Path | None = None
    trace_limit: int | None = None
    trace_ports: bool | None = None
    trace_interrupts: bool | None = None
    trace_i2c: bool | None = None
    trace_spi: bool | None = None
    dump_iram: bool | None = None
    dump_sfr: bool | None = None
    tight_loop_detect: bool | None = None


def load_project_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path).resolve()
    document = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ProjectConfigError("project config must be a JSON object")

    image_value = document.get("image") or document.get("source")
    if not isinstance(image_value, str):
        raise ProjectConfigError("project config requires 'image' or 'source'")

    base_dir = config_path.parent
    return ProjectConfig(
        path=config_path,
        image=(base_dir / image_value).resolve(),
        format=_optional_string(document.get("format")),
        origin=_optional_int(document.get("origin")),
        entry=_optional_int(document.get("entry")),
        max_instructions=_optional_int(document.get("max_instructions")),
        serial_input=_optional_string(document.get("serial_input")),
        serial_input_hex=_optional_string(document.get("serial_input_hex")),
        runtime=_optional_path(base_dir, document.get("runtime")),
        symbols=_optional_path(base_dir, document.get("symbols")),
        breakpoints=tuple(_ensure_string_list(document.get("breakpoints", []))),
        watchpoints=tuple(_ensure_string_list(document.get("watchpoints", []))),
        watch_log=_optional_bool(document.get("watch_log")),
        inspect=tuple(_ensure_string_list(document.get("inspect", []))),
        step_limit=_optional_int(document.get("step_limit")),
        dump_direct=tuple(_ensure_string_list(document.get("dump_direct", []))),
        dump_xram=tuple(_ensure_string_list(document.get("dump_xram", []))),
        trace_file=_optional_path(base_dir, document.get("trace_file")),
        trace_limit=_optional_int(document.get("trace_limit")),
        trace_ports=_optional_bool(document.get("trace_ports")),
        trace_interrupts=_optional_bool(document.get("trace_interrupts")),
        trace_i2c=_optional_bool(document.get("trace_i2c")),
        trace_spi=_optional_bool(document.get("trace_spi")),
        dump_iram=_optional_bool(document.get("dump_iram")),
        dump_sfr=_optional_bool(document.get("dump_sfr")),
        tight_loop_detect=_optional_bool(document.get("tight_loop_detect")),
    )


def _optional_path(base_dir: Path, value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectConfigError("project path values must be strings")
    return (base_dir / value).resolve()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectConfigError("project string values must be strings")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectConfigError("project integer values must be integers")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ProjectConfigError("project boolean values must be true/false")
    return value


def _ensure_string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProjectConfigError("project breakpoints must be a string list")
    return list(value)
