from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cpu import MCS51


class RuntimeConfigError(ValueError):
    """Raised when a runtime event file cannot be parsed."""


@dataclass(frozen=True)
class RuntimeEvent:
    tick: int
    event_type: str
    text: str | None = None
    hex_bytes: str | None = None
    count: int = 1


def load_runtime_events(path: str | Path) -> list[RuntimeEvent]:
    config_path = Path(path)
    document = json.loads(config_path.read_text(encoding="utf-8"))
    raw_events = document["events"] if isinstance(document, dict) else document
    if not isinstance(raw_events, list):
        raise RuntimeConfigError("runtime config must be a list or an object with an 'events' list")

    events: list[RuntimeEvent] = []
    for index, item in enumerate(raw_events):
        if not isinstance(item, dict):
            raise RuntimeConfigError(f"runtime event #{index} must be an object")

        tick = item.get("tick")
        event_type = item.get("type")
        if not isinstance(tick, int) or tick < 0:
            raise RuntimeConfigError(f"runtime event #{index} has invalid tick")
        if not isinstance(event_type, str):
            raise RuntimeConfigError(f"runtime event #{index} has invalid type")

        events.append(
            RuntimeEvent(
                tick=tick,
                event_type=event_type.lower(),
                text=item.get("text"),
                hex_bytes=item.get("hex"),
                count=int(item.get("count", 1)),
            )
        )

    events.sort(key=lambda event: event.tick)
    return events


def make_runtime_hook(events: list[RuntimeEvent]) -> Callable[[MCS51, int], None]:
    grouped: dict[int, list[RuntimeEvent]] = {}
    for event in events:
        grouped.setdefault(event.tick, []).append(event)

    def hook(cpu: MCS51, tick: int) -> None:
        for event in grouped.get(tick, ()):
            _apply_event(cpu, event)

    return hook


def _apply_event(cpu: MCS51, event: RuntimeEvent) -> None:
    if event.event_type == "serial":
        if event.text:
            cpu.queue_serial_input(event.text)
        if event.hex_bytes:
            cpu.queue_serial_input(_parse_hex_bytes(event.hex_bytes))
        return

    if event.event_type == "extint0":
        cpu.request_external_interrupt(0)
        return

    if event.event_type == "extint1":
        cpu.request_external_interrupt(1)
        return

    if event.event_type == "extint0_low":
        cpu.set_external_interrupt_line(0, 0)
        return

    if event.event_type == "extint0_high":
        cpu.set_external_interrupt_line(0, 1)
        return

    if event.event_type == "extint1_low":
        cpu.set_external_interrupt_line(1, 0)
        return

    if event.event_type == "extint1_high":
        cpu.set_external_interrupt_line(1, 1)
        return

    if event.event_type == "counter0":
        cpu.pulse_external_counter(0, event.count)
        return

    if event.event_type == "counter1":
        cpu.pulse_external_counter(1, event.count)
        return

    if event.event_type == "i2c_response":
        cpu.i2c.inject_response(_parse_hex_bytes(event.hex_bytes))
        return

    if event.event_type == "spi_response":
        cpu.spi.inject_response(_parse_hex_bytes(event.hex_bytes))
        return

    raise RuntimeConfigError(f"unsupported runtime event type: {event.event_type}")


def _parse_hex_bytes(value: str) -> bytes:
    compact = value.replace(" ", "").replace(",", "").replace("_", "")
    if compact.startswith("0x"):
        compact = compact[2:]
    if len(compact) % 2:
        raise RuntimeConfigError("runtime hex input must contain an even number of digits")
    return bytes.fromhex(compact)
