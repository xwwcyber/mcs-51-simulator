from __future__ import annotations


def encode_intel_hex(data: bytes, origin: int = 0, record_size: int = 16) -> str:
    lines: list[str] = []
    upper = None

    for offset in range(0, len(data), record_size):
        chunk = data[offset : offset + record_size]
        absolute = origin + offset
        absolute_upper = absolute >> 16
        if upper != absolute_upper:
            upper = absolute_upper
            if upper:
                lines.append(_build_record(0x0000, 0x04, bytes([(upper >> 8) & 0xFF, upper & 0xFF])))

        lines.append(_build_record(absolute & 0xFFFF, 0x00, chunk))

    lines.append(":00000001FF")
    return "\n".join(lines) + "\n"


def _build_record(address: int, record_type: int, payload: bytes) -> str:
    header = bytes(
        [
            len(payload),
            (address >> 8) & 0xFF,
            address & 0xFF,
            record_type & 0xFF,
        ]
    )
    checksum = (-sum(header + payload)) & 0xFF
    return f":{(header + payload).hex().upper()}{checksum:02X}"
