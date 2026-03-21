from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .assembler import BIT_SYMBOLS, SFR_SYMBOLS
from .cpu import MCS51
from .disasm import disassemble_instruction


class DebugConfigError(ValueError):
    """Raised when debug options or symbol files are invalid."""


@dataclass(frozen=True)
class SymbolFile:
    symbols: dict[str, int]
    source_map: dict[int, tuple[int | None, str]]


@dataclass(frozen=True)
class WatchTarget:
    kind: str
    address: int
    name: str
    trigger: str = "change"
    halt: bool = True


@dataclass(frozen=True)
class WatchEvent:
    instruction_index: int
    pc: int
    target: WatchTarget
    previous_value: int
    current_value: int


def load_symbol_file(path: str | Path) -> SymbolFile:
    symbol_path = Path(path)
    document = json.loads(symbol_path.read_text(encoding="utf-8"))
    raw_symbols = document.get("symbols", {})
    raw_source_map = document.get("source_map", {})
    if not isinstance(raw_symbols, dict) or not isinstance(raw_source_map, dict):
        raise DebugConfigError(f"{symbol_path}: invalid symbol file structure")

    symbols = {str(name).upper(): int(value) for name, value in raw_symbols.items()}
    source_map: dict[int, tuple[int | None, str]] = {}
    for address, payload in raw_source_map.items():
        parsed_address = int(str(address), 0)
        if not isinstance(payload, dict):
            raise DebugConfigError(f"{symbol_path}: invalid source_map entry for {address}")
        line_number = payload.get("line_number")
        text = payload.get("text", "")
        source_map[parsed_address] = (
            int(line_number) if isinstance(line_number, int) else None,
            str(text),
        )

    return SymbolFile(symbols=symbols, source_map=source_map)


def merge_symbol_info(
    program_symbols: dict[str, int] | None,
    program_source_map: dict[int, object] | None,
    symbol_file: SymbolFile | None,
) -> tuple[dict[str, int], dict[int, tuple[int | None, str]]]:
    symbols = {**(program_symbols or {})}
    source_map = _normalize_source_map(program_source_map)

    if symbol_file is not None:
        symbols.update(symbol_file.symbols)
        source_map.update(symbol_file.source_map)

    return symbols, source_map


def resolve_breakpoints(
    specs: list[str] | tuple[str, ...],
    symbols: dict[str, int] | None = None,
) -> dict[int, str]:
    resolved: dict[int, str] = {}
    lookup = {**(symbols or {})}
    for spec in specs:
        token = spec.strip()
        if not token:
            continue
        try:
            address = _parse_address(token)
            resolved[address] = f"0x{address:04X}"
            continue
        except ValueError:
            pass

        upper = token.upper()
        if upper not in lookup:
            raise DebugConfigError(f"Unknown breakpoint symbol: {token}")
        resolved[lookup[upper] & 0xFFFF] = token

    return resolved


def make_breakpoint_hook(breakpoints: dict[int, str]):
    def hook(cpu: MCS51, _tick: int) -> str | None:
        name = breakpoints.get(cpu.pc)
        if name is None:
            return None
        return f"breakpoint:{name}@0x{cpu.pc:04X}"

    return hook


class StepLimiter:
    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise DebugConfigError("step limit must be positive")
        self.limit = limit

    def callback(
        self,
        _cpu: MCS51,
        _previous_pc: int,
        _opcode: int,
        instruction_index: int,
    ) -> str | None:
        if instruction_index >= self.limit:
            return f"step_limit:{self.limit}"
        return None


class WatchpointMonitor:
    def __init__(self, targets: list[WatchTarget]) -> None:
        if not targets:
            raise DebugConfigError("watchpoint list must not be empty")
        self.targets = targets
        self.values: dict[WatchTarget, int] = {}
        self.events: list[WatchEvent] = []
        self.has_log_only_targets = any(not target.halt for target in targets)

    def prime(self, cpu: MCS51) -> None:
        for target in self.targets:
            self.values[target] = _read_watch_value(cpu, target)

    def callback(
        self,
        cpu: MCS51,
        previous_pc: int,
        _opcode: int,
        instruction_index: int,
    ) -> str | None:
        for target in self.targets:
            current = _read_watch_value(cpu, target)
            previous = self.values.get(target, current)
            should_fire = _watch_triggered(target, previous, current)
            self.values[target] = current
            if should_fire:
                event = WatchEvent(
                    instruction_index=instruction_index,
                    pc=previous_pc,
                    target=target,
                    previous_value=previous,
                    current_value=current,
                )
                self.events.append(event)
                if target.halt:
                    return _halt_reason_for_watch_event(event)
        return None


class InstructionTracer:
    def __init__(
        self,
        path: str | Path,
        code_memory: bytes | bytearray,
        symbols: dict[str, int] | None = None,
        source_map: dict[int, tuple[int | None, str]] | None = None,
        limit: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.code_memory = code_memory
        self.limit = limit
        self.lines_written = 0
        self.symbols = symbols or {}
        self.symbols_by_address: dict[int, list[str]] = {}
        self.source_map = source_map or {}

        for name, address in self.symbols.items():
            self.symbols_by_address.setdefault(address & 0xFFFF, []).append(name)

        self.handle = self.path.open("w", encoding="utf-8")
        self.handle.write("# instruction trace\n")

    def close(self) -> None:
        self.handle.close()

    def callback(self, cpu: MCS51, previous_pc: int, opcode: int, instruction_index: int) -> None:
        if self.limit is not None and self.lines_written >= self.limit:
            return

        disasm = disassemble_instruction(self.code_memory, previous_pc, self.symbols)
        labels = ",".join(sorted(self.symbols_by_address.get(previous_pc, [])))
        source_line = self.source_map.get(previous_pc)
        suffix = ""
        if labels:
            suffix += f" LABEL={labels}"
        if source_line:
            line_number, text = source_line
            if line_number is not None:
                suffix += f" SRC={line_number}:{text}"
            else:
                suffix += f" SRC={text}"

        self.handle.write(
            f"[{instruction_index:06d}] "
            f"PC=0x{previous_pc:04X} OP=0x{opcode:02X} "
            f"ASM={disasm.text} "
            f"A=0x{cpu.acc:02X} B=0x{cpu.b:02X} PSW=0x{cpu.psw:02X} "
            f"SP=0x{cpu.sp:02X} DPTR=0x{cpu.dptr:04X}{suffix}\n"
        )
        self.lines_written += 1
        return None


def resolve_watchpoints(
    specs: list[str] | tuple[str, ...],
    symbols: dict[str, int] | None = None,
) -> list[WatchTarget]:
    resolved: list[WatchTarget] = []
    lookup = {**SFR_SYMBOLS, **BIT_SYMBOLS, **(symbols or {})}
    for spec in specs:
        token = spec.strip()
        if not token:
            continue
        trigger, halt, target_spec = _split_watch_spec(token)
        lower_target = target_spec.lower()

        if lower_target.startswith("xram:"):
            address = _parse_address(target_spec.split(":", 1)[1]) & 0xFFFF
            if trigger in {"rise", "fall"}:
                raise DebugConfigError(f"Edge-triggered watch requires a bit target: {token}")
            resolved.append(WatchTarget("xram", address, target_spec, trigger=trigger, halt=halt))
            continue
        if lower_target.startswith("bit:"):
            bit_address = _resolve_bit_spec(target_spec.split(":", 1)[1], lookup)
            resolved.append(WatchTarget("bit", bit_address, target_spec, trigger=trigger, halt=halt))
            continue
        if lower_target.startswith("direct:"):
            address = _resolve_direct_spec(target_spec.split(":", 1)[1], lookup)
            if trigger in {"rise", "fall"}:
                raise DebugConfigError(f"Edge-triggered watch requires a bit target: {token}")
            resolved.append(WatchTarget("direct", address, target_spec, trigger=trigger, halt=halt))
            continue

        if "." in target_spec or target_spec.upper() in BIT_SYMBOLS:
            bit_address = _resolve_bit_spec(target_spec, lookup)
            resolved.append(WatchTarget("bit", bit_address, target_spec, trigger=trigger, halt=halt))
            continue

        if trigger in {"rise", "fall"}:
            raise DebugConfigError(f"Edge-triggered watch requires a bit target: {token}")
        address = _resolve_direct_spec(target_spec, lookup)
        resolved.append(WatchTarget("direct", address, target_spec, trigger=trigger, halt=halt))

    return resolved


def _normalize_source_map(
    program_source_map: dict[int, object] | None,
) -> dict[int, tuple[int | None, str]]:
    normalized: dict[int, tuple[int | None, str]] = {}
    if not program_source_map:
        return normalized

    for address, location in program_source_map.items():
        line_number = getattr(location, "line_number", None)
        text = getattr(location, "text", str(location))
        normalized[int(address)] = (line_number, text)
    return normalized


def _parse_address(token: str) -> int:
    upper = token.upper()
    if upper.endswith("H") and upper[:-1]:
        return int(upper[:-1], 16)
    return int(token, 0)


def _split_watch_spec(spec: str) -> tuple[str, bool, str]:
    trigger = "change"
    halt = True
    remaining = spec

    while ":" in remaining:
        prefix, tail = remaining.split(":", 1)
        lower = prefix.lower()
        if lower == "log":
            halt = False
            remaining = tail
            continue
        if lower == "stop":
            halt = True
            remaining = tail
            continue
        if lower in {"change", "rise", "fall"}:
            trigger = lower
            remaining = tail
            continue
        break

    return trigger, halt, remaining


def _resolve_direct_spec(token: str, lookup: dict[str, int]) -> int:
    upper = token.upper()
    if upper in lookup:
        return lookup[upper] & 0xFF
    return _parse_address(token) & 0xFF


def _resolve_bit_spec(token: str, lookup: dict[str, int]) -> int:
    upper = token.upper()
    if "." in upper:
        base, bit = upper.split(".", 1)
        if base not in SFR_SYMBOLS:
            raise DebugConfigError(f"Unknown bit-addressable SFR {base}")
        bit_value = _parse_address(bit)
        if bit_value < 0 or bit_value > 7:
            raise DebugConfigError(f"Bit index out of range in {token}")
        return (SFR_SYMBOLS[base] & 0xF8) | bit_value
    if upper in lookup:
        return lookup[upper] & 0xFF
    return _parse_address(token) & 0xFF


def _read_watch_value(cpu: MCS51, target: WatchTarget) -> int:
    if target.kind == "direct":
        return cpu.read_direct(target.address)
    if target.kind == "bit":
        return cpu._read_bit(target.address)
    if target.kind == "xram":
        return cpu.xram[target.address]
    raise DebugConfigError(f"Unsupported watch target kind: {target.kind}")


def _format_watch_value(kind: str, value: int) -> str:
    if kind == "bit":
        return str(value & 0x01)
    return f"0x{value & 0xFF:02X}"


def _watch_triggered(target: WatchTarget, previous: int, current: int) -> bool:
    if target.trigger == "change":
        return current != previous
    if target.trigger == "rise":
        return previous == 0 and current != 0
    if target.trigger == "fall":
        return previous != 0 and current == 0
    raise DebugConfigError(f"Unsupported watch trigger: {target.trigger}")


def _halt_reason_for_watch_event(event: WatchEvent) -> str:
    if event.target.trigger == "change":
        return f"watch:{event.target.name}={_format_watch_value(event.target.kind, event.current_value)}"
    return (
        f"watch:{event.target.trigger}:{event.target.name}="
        f"{_format_watch_value(event.target.kind, event.current_value)}"
    )


def format_watch_event(event: WatchEvent) -> str:
    if event.target.trigger == "change":
        action = (
            f"changed {_format_watch_value(event.target.kind, event.previous_value)} -> "
            f"{_format_watch_value(event.target.kind, event.current_value)}"
        )
    elif event.target.trigger == "rise":
        action = "rose 0 -> 1"
    elif event.target.trigger == "fall":
        action = "fell 1 -> 0"
    else:
        action = f"{event.target.trigger} {_format_watch_value(event.target.kind, event.current_value)}"
    return f"{event.target.name} {action}"
