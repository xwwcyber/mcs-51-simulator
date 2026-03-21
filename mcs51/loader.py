from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .assembler import AssemblyError, assemble_file

class LoaderError(ValueError):
    """Raised when a HEX or BIN image cannot be loaded."""


@dataclass(frozen=True)
class LoadedProgram:
    code_memory: bytes
    entry_point: int
    low_address: int
    high_address: int
    format: str
    source_name: str | None = None
    symbols: dict[str, int] | None = None
    source_map: dict[int, object] | None = None


def _decode_hex_line(line: str, line_number: int) -> tuple[int, int, int, bytes]:
    if not line.startswith(":"):
        raise LoaderError(f"HEX line {line_number} does not start with ':'")

    try:
        raw = bytes.fromhex(line[1:])
    except ValueError as exc:
        raise LoaderError(f"HEX line {line_number} is not valid hexadecimal") from exc

    if len(raw) < 5:
        raise LoaderError(f"HEX line {line_number} is too short")

    length = raw[0]
    expected = length + 5
    if len(raw) != expected:
        raise LoaderError(
            f"HEX line {line_number} has length {len(raw)}, expected {expected}"
        )

    if sum(raw) & 0xFF:
        raise LoaderError(f"HEX line {line_number} has an invalid checksum")

    address = (raw[1] << 8) | raw[2]
    record_type = raw[3]
    data = raw[4 : 4 + length]
    return length, address, record_type, data


def load_intel_hex(path: Path, entry_point: int | None = None) -> LoadedProgram:
    code = bytearray(0x10000)
    upper_address = 0
    low_address = 0xFFFF
    high_address = 0
    seen_data = False

    lines = path.read_text(encoding="ascii").splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        length, address, record_type, data = _decode_hex_line(line, line_number)

        if record_type == 0x00:
            absolute = upper_address + address
            if absolute + length > len(code):
                raise LoaderError(
                    f"HEX line {line_number} targets address 0x{absolute:05X}, "
                    "outside 8051 code memory"
                )
            code[absolute : absolute + length] = data
            seen_data = True
            low_address = min(low_address, absolute)
            high_address = max(high_address, absolute + length - 1)
        elif record_type == 0x01:
            break
        elif record_type == 0x02:
            if length != 2:
                raise LoaderError(f"HEX line {line_number} has invalid type 02 length")
            upper_address = ((data[0] << 8) | data[1]) << 4
        elif record_type == 0x04:
            if length != 2:
                raise LoaderError(f"HEX line {line_number} has invalid type 04 length")
            upper_address = ((data[0] << 8) | data[1]) << 16
        elif record_type in (0x03, 0x05):
            continue
        else:
            raise LoaderError(
                f"HEX line {line_number} has unsupported record type 0x{record_type:02X}"
            )

    if not seen_data:
        raise LoaderError(f"{path} does not contain any data records")

    return LoadedProgram(
        code_memory=bytes(code),
        entry_point=low_address if entry_point is None else entry_point & 0xFFFF,
        low_address=low_address,
        high_address=high_address,
        format="hex",
        source_name=str(path),
    )


def load_binary(
    path: Path, origin: int = 0, entry_point: int | None = None
) -> LoadedProgram:
    data = path.read_bytes()
    if origin < 0 or origin > 0xFFFF:
        raise LoaderError(f"Origin out of range: 0x{origin:X}")
    if origin + len(data) > 0x10000:
        raise LoaderError("BIN image does not fit into 64 KiB code memory")

    code = bytearray(0x10000)
    code[origin : origin + len(data)] = data

    return LoadedProgram(
        code_memory=bytes(code),
        entry_point=origin if entry_point is None else entry_point & 0xFFFF,
        low_address=origin,
        high_address=origin + len(data) - 1 if data else origin,
        format="bin",
        source_name=str(path),
    )


def load_program(
    path: str | Path,
    fmt: str = "auto",
    origin: int = 0,
    entry_point: int | None = None,
) -> LoadedProgram:
    program_path = Path(path)
    selected = fmt.lower()
    if selected == "auto":
        suffix = program_path.suffix.lower()
        if suffix in {".hex", ".ihx"}:
            selected = "hex"
        elif suffix in {".asm", ".a51", ".s", ".s51"}:
            selected = "asm"
        else:
            selected = "bin"

    if selected == "hex":
        return load_intel_hex(program_path, entry_point=entry_point)
    if selected == "bin":
        return load_binary(program_path, origin=origin, entry_point=entry_point)
    if selected == "asm":
        try:
            result = assemble_file(program_path)
        except AssemblyError as exc:
            raise LoaderError(str(exc)) from exc
        return LoadedProgram(
            code_memory=result.code_memory,
            entry_point=result.entry_point if entry_point is None else entry_point & 0xFFFF,
            low_address=result.low_address,
            high_address=result.high_address,
            format="asm",
            source_name=result.source_name,
            symbols=result.symbols,
            source_map=result.source_map,
        )
    raise LoaderError(f"Unsupported format: {fmt}")
