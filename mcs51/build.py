from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assembler import AssemblyError, assemble_file
from .formats import encode_intel_hex


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build 8051 assembly source into BIN and HEX")
    parser.add_argument("source", type=Path, help="Assembly source file")
    parser.add_argument("--bin-out", type=Path, help="Output BIN file path")
    parser.add_argument("--hex-out", type=Path, help="Output HEX file path")
    parser.add_argument("--sym-out", type=Path, help="Output symbol JSON file path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = assemble_file(args.source)
    except AssemblyError as exc:
        parser.error(str(exc))

    bin_out = args.bin_out or args.source.with_suffix(".bin")
    hex_out = args.hex_out or args.source.with_suffix(".hex")
    sym_out = args.sym_out or args.source.with_suffix(".sym.json")

    low = result.low_address
    high = result.high_address
    payload = result.code_memory[low : high + 1]

    bin_out.write_bytes(payload)
    hex_out.write_text(encode_intel_hex(payload, origin=low), encoding="ascii")
    sym_out.write_text(
        json.dumps(
            {
                "source": result.source_name,
                "entry_point": result.entry_point,
                "low_address": result.low_address,
                "high_address": result.high_address,
                "symbols": result.symbols,
                "source_map": {
                    f"0x{address:04X}": {
                        "line_number": location.line_number,
                        "text": location.text,
                    }
                    for address, location in sorted(result.source_map.items())
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Built {args.source.name}: "
        f"0x{low:04X}-0x{high:04X} -> "
        f"{bin_out.name}, {hex_out.name}, {sym_out.name}"
    )
    if low:
        print(f"BIN origin: 0x{low:04X}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
