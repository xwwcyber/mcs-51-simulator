from __future__ import annotations

from pathlib import Path

from mcs51.assembler import assemble_file
from mcs51.formats import encode_intel_hex
import json


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def build_example(source_path: Path) -> None:
    result = assemble_file(source_path)
    payload = result.code_memory[result.low_address : result.high_address + 1]

    source_path.with_suffix(".bin").write_bytes(payload)
    source_path.with_suffix(".hex").write_text(
        encode_intel_hex(payload, origin=result.low_address),
        encoding="ascii",
    )
    source_path.with_suffix(".sym.json").write_text(
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


def main() -> None:
    for source_path in sorted(EXAMPLES.glob("*.asm")):
        build_example(source_path)


if __name__ == "__main__":
    main()
