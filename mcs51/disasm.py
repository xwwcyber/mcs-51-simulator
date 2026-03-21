from __future__ import annotations

from dataclasses import dataclass

from .assembler import BIT_SYMBOLS


@dataclass(frozen=True)
class Disassembly:
    address: int
    size: int
    opcode: int
    text: str


_DIRECT_NAMES = {
    0x80: "P0",
    0x81: "SP",
    0x82: "DPL",
    0x83: "DPH",
    0x87: "PCON",
    0x88: "TCON",
    0x89: "TMOD",
    0x8A: "TL0",
    0x8B: "TL1",
    0x8C: "TH0",
    0x8D: "TH1",
    0x90: "P1",
    0x98: "SCON",
    0x99: "SBUF",
    0xA0: "P2",
    0xA8: "IE",
    0xB0: "P3",
    0xB8: "IP",
    0xD0: "PSW",
    0xE0: "ACC",
    0xF0: "B",
}
_BIT_NAMES = {address: name for name, address in BIT_SYMBOLS.items()}
_BIT_BASE_NAMES = {
    address & 0xF8: name
    for address, name in _DIRECT_NAMES.items()
    if (address & 0x07) == 0 and address >= 0x80
}
_ACC_SOURCE_MNEMONICS = {
    0x20: "ADD",
    0x30: "ADDC",
    0x40: "ORL",
    0x50: "ANL",
    0x60: "XRL",
    0x90: "SUBB",
}


def disassemble_instruction(
    code_memory: bytes | bytearray,
    address: int,
    symbols: dict[str, int] | None = None,
) -> Disassembly:
    pc = address & 0xFFFF
    opcode = code_memory[pc]
    symbols_by_address = _symbols_by_address(symbols)

    def byte(offset: int) -> int:
        return code_memory[(pc + offset) & 0xFFFF]

    def word(offset: int) -> int:
        return (byte(offset) << 8) | byte(offset + 1)

    def rel(offset_index: int, size: int) -> int:
        displacement = byte(offset_index)
        if displacement & 0x80:
            displacement -= 0x100
        return (pc + size + displacement) & 0xFFFF

    if opcode == 0x00:
        return Disassembly(pc, 1, opcode, "NOP")
    if opcode & 0x1F == 0x01:
        low = byte(1)
        next_pc = (pc + 2) & 0xFFFF
        target = (next_pc & 0xF800) | ((opcode & 0xE0) << 3) | low
        return Disassembly(pc, 2, opcode, f"AJMP {_format_code_target(target, symbols_by_address)}")
    if opcode == 0x02:
        target = word(1)
        return Disassembly(pc, 3, opcode, f"LJMP {_format_code_target(target, symbols_by_address)}")
    if opcode == 0x03:
        return Disassembly(pc, 1, opcode, "RR A")
    if 0x04 <= opcode <= 0x0F:
        if opcode == 0x04:
            return Disassembly(pc, 1, opcode, "INC A")
        if opcode == 0x05:
            return Disassembly(pc, 2, opcode, f"INC {_format_direct(byte(1))}")
        if opcode in (0x06, 0x07):
            return Disassembly(pc, 1, opcode, f"INC @R{opcode & 0x01}")
        return Disassembly(pc, 1, opcode, f"INC R{opcode & 0x07}")
    if opcode == 0x10:
        target = rel(2, 3)
        return Disassembly(
            pc,
            3,
            opcode,
            f"JBC {_format_bit(byte(1))},{_format_code_target(target, symbols_by_address)}",
        )
    if opcode & 0x1F == 0x11:
        low = byte(1)
        next_pc = (pc + 2) & 0xFFFF
        target = (next_pc & 0xF800) | ((opcode & 0xE0) << 3) | low
        return Disassembly(pc, 2, opcode, f"ACALL {_format_code_target(target, symbols_by_address)}")
    if opcode == 0x12:
        target = word(1)
        return Disassembly(pc, 3, opcode, f"LCALL {_format_code_target(target, symbols_by_address)}")
    if opcode == 0x13:
        return Disassembly(pc, 1, opcode, "RRC A")
    if 0x14 <= opcode <= 0x1F:
        if opcode == 0x14:
            return Disassembly(pc, 1, opcode, "DEC A")
        if opcode == 0x15:
            return Disassembly(pc, 2, opcode, f"DEC {_format_direct(byte(1))}")
        if opcode in (0x16, 0x17):
            return Disassembly(pc, 1, opcode, f"DEC @R{opcode & 0x01}")
        return Disassembly(pc, 1, opcode, f"DEC R{opcode & 0x07}")
    if opcode == 0x20:
        target = rel(2, 3)
        return Disassembly(
            pc,
            3,
            opcode,
            f"JB {_format_bit(byte(1))},{_format_code_target(target, symbols_by_address)}",
        )
    if opcode == 0x22:
        return Disassembly(pc, 1, opcode, "RET")
    if opcode == 0x23:
        return Disassembly(pc, 1, opcode, "RL A")
    if 0x24 <= opcode <= 0x2F:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"ADD A,{operand}")
    if opcode == 0x30:
        target = rel(2, 3)
        return Disassembly(
            pc,
            3,
            opcode,
            f"JNB {_format_bit(byte(1))},{_format_code_target(target, symbols_by_address)}",
        )
    if opcode == 0x32:
        return Disassembly(pc, 1, opcode, "RETI")
    if opcode == 0x33:
        return Disassembly(pc, 1, opcode, "RLC A")
    if 0x34 <= opcode <= 0x3F:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"ADDC A,{operand}")
    if opcode == 0x40:
        return Disassembly(
            pc,
            2,
            opcode,
            f"JC {_format_code_target(rel(1, 2), symbols_by_address)}",
        )
    if opcode == 0x42:
        return Disassembly(pc, 2, opcode, f"ORL {_format_direct(byte(1))},A")
    if opcode == 0x43:
        return Disassembly(pc, 3, opcode, f"ORL {_format_direct(byte(1))},#{_format_immediate(byte(2))}")
    if 0x44 <= opcode <= 0x4F:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"ORL A,{operand}")
    if opcode == 0x50:
        return Disassembly(
            pc,
            2,
            opcode,
            f"JNC {_format_code_target(rel(1, 2), symbols_by_address)}",
        )
    if opcode == 0x52:
        return Disassembly(pc, 2, opcode, f"ANL {_format_direct(byte(1))},A")
    if opcode == 0x53:
        return Disassembly(pc, 3, opcode, f"ANL {_format_direct(byte(1))},#{_format_immediate(byte(2))}")
    if 0x54 <= opcode <= 0x5F:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"ANL A,{operand}")
    if opcode == 0x60:
        return Disassembly(
            pc,
            2,
            opcode,
            f"JZ {_format_code_target(rel(1, 2), symbols_by_address)}",
        )
    if opcode == 0x62:
        return Disassembly(pc, 2, opcode, f"XRL {_format_direct(byte(1))},A")
    if opcode == 0x63:
        return Disassembly(pc, 3, opcode, f"XRL {_format_direct(byte(1))},#{_format_immediate(byte(2))}")
    if 0x64 <= opcode <= 0x6F:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"XRL A,{operand}")
    if opcode == 0x70:
        return Disassembly(
            pc,
            2,
            opcode,
            f"JNZ {_format_code_target(rel(1, 2), symbols_by_address)}",
        )
    if opcode == 0x72:
        return Disassembly(pc, 2, opcode, f"ORL C,{_format_bit(byte(1))}")
    if opcode == 0x73:
        return Disassembly(pc, 1, opcode, "JMP @A+DPTR")
    if opcode == 0x74:
        return Disassembly(pc, 2, opcode, f"MOV A,#{_format_immediate(byte(1))}")
    if opcode == 0x75:
        return Disassembly(
            pc,
            3,
            opcode,
            f"MOV {_format_direct(byte(1))},#{_format_immediate(byte(2))}",
        )
    if opcode in (0x76, 0x77):
        return Disassembly(pc, 2, opcode, f"MOV @R{opcode & 0x01},#{_format_immediate(byte(1))}")
    if 0x78 <= opcode <= 0x7F:
        return Disassembly(pc, 2, opcode, f"MOV R{opcode & 0x07},#{_format_immediate(byte(1))}")
    if opcode == 0x80:
        return Disassembly(
            pc,
            2,
            opcode,
            f"SJMP {_format_code_target(rel(1, 2), symbols_by_address)}",
        )
    if opcode == 0x82:
        return Disassembly(pc, 2, opcode, f"ANL C,{_format_bit(byte(1))}")
    if opcode == 0x83:
        return Disassembly(pc, 1, opcode, "MOVC A,@A+PC")
    if opcode == 0x84:
        return Disassembly(pc, 1, opcode, "DIV AB")
    if opcode == 0x85:
        return Disassembly(pc, 3, opcode, f"MOV {_format_direct(byte(2))},{_format_direct(byte(1))}")
    if opcode in (0x86, 0x87):
        return Disassembly(pc, 2, opcode, f"MOV {_format_direct(byte(1))},@R{opcode & 0x01}")
    if 0x88 <= opcode <= 0x8F:
        return Disassembly(pc, 2, opcode, f"MOV {_format_direct(byte(1))},R{opcode & 0x07}")
    if opcode == 0x90:
        target = word(1)
        label = _label_for_code(target, symbols_by_address)
        operand = f"#{label}" if label else f"#0x{target:04X}"
        return Disassembly(pc, 3, opcode, f"MOV DPTR,{operand}")
    if opcode == 0x92:
        return Disassembly(pc, 2, opcode, f"MOV {_format_bit(byte(1))},C")
    if opcode == 0x93:
        return Disassembly(pc, 1, opcode, "MOVC A,@A+DPTR")
    if 0x94 <= opcode <= 0x9F:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"SUBB A,{operand}")
    if opcode == 0xA0:
        return Disassembly(pc, 2, opcode, f"ORL C,/{_format_bit(byte(1))}")
    if opcode == 0xA2:
        return Disassembly(pc, 2, opcode, f"MOV C,{_format_bit(byte(1))}")
    if opcode == 0xA3:
        return Disassembly(pc, 1, opcode, "INC DPTR")
    if opcode == 0xA4:
        return Disassembly(pc, 1, opcode, "MUL AB")
    if opcode == 0xA5:
        return Disassembly(pc, 1, opcode, "DB 0xA5")
    if opcode in (0xA6, 0xA7):
        return Disassembly(pc, 2, opcode, f"MOV @R{opcode & 0x01},{_format_direct(byte(1))}")
    if 0xA8 <= opcode <= 0xAF:
        return Disassembly(pc, 2, opcode, f"MOV R{opcode & 0x07},{_format_direct(byte(1))}")
    if opcode == 0xB0:
        return Disassembly(pc, 2, opcode, f"ANL C,/{_format_bit(byte(1))}")
    if opcode == 0xB2:
        return Disassembly(pc, 2, opcode, f"CPL {_format_bit(byte(1))}")
    if opcode == 0xB3:
        return Disassembly(pc, 1, opcode, "CPL C")
    if opcode == 0xB4:
        return Disassembly(
            pc,
            3,
            opcode,
            f"CJNE A,#{_format_immediate(byte(1))},{_format_code_target(rel(2, 3), symbols_by_address)}",
        )
    if opcode == 0xB5:
        return Disassembly(
            pc,
            3,
            opcode,
            f"CJNE A,{_format_direct(byte(1))},{_format_code_target(rel(2, 3), symbols_by_address)}",
        )
    if opcode in (0xB6, 0xB7):
        return Disassembly(
            pc,
            3,
            opcode,
            f"CJNE @R{opcode & 0x01},#{_format_immediate(byte(1))},{_format_code_target(rel(2, 3), symbols_by_address)}",
        )
    if 0xB8 <= opcode <= 0xBF:
        return Disassembly(
            pc,
            3,
            opcode,
            f"CJNE R{opcode & 0x07},#{_format_immediate(byte(1))},{_format_code_target(rel(2, 3), symbols_by_address)}",
        )
    if opcode == 0xC0:
        return Disassembly(pc, 2, opcode, f"PUSH {_format_direct(byte(1))}")
    if opcode == 0xC2:
        return Disassembly(pc, 2, opcode, f"CLR {_format_bit(byte(1))}")
    if opcode == 0xC3:
        return Disassembly(pc, 1, opcode, "CLR C")
    if opcode == 0xC4:
        return Disassembly(pc, 1, opcode, "SWAP A")
    if opcode == 0xC5:
        return Disassembly(pc, 2, opcode, f"XCH A,{_format_direct(byte(1))}")
    if opcode in (0xC6, 0xC7):
        return Disassembly(pc, 1, opcode, f"XCH A,@R{opcode & 0x01}")
    if 0xC8 <= opcode <= 0xCF:
        return Disassembly(pc, 1, opcode, f"XCH A,R{opcode & 0x07}")
    if opcode == 0xD0:
        return Disassembly(pc, 2, opcode, f"POP {_format_direct(byte(1))}")
    if opcode == 0xD2:
        return Disassembly(pc, 2, opcode, f"SETB {_format_bit(byte(1))}")
    if opcode == 0xD3:
        return Disassembly(pc, 1, opcode, "SETB C")
    if opcode == 0xD4:
        return Disassembly(pc, 1, opcode, "DA A")
    if opcode == 0xD5:
        return Disassembly(
            pc,
            3,
            opcode,
            f"DJNZ {_format_direct(byte(1))},{_format_code_target(rel(2, 3), symbols_by_address)}",
        )
    if opcode in (0xD6, 0xD7):
        return Disassembly(pc, 1, opcode, f"XCHD A,@R{opcode & 0x01}")
    if 0xD8 <= opcode <= 0xDF:
        return Disassembly(
            pc,
            2,
            opcode,
            f"DJNZ R{opcode & 0x07},{_format_code_target(rel(1, 2), symbols_by_address)}",
        )
    if opcode == 0xE0:
        return Disassembly(pc, 1, opcode, "MOVX A,@DPTR")
    if opcode in (0xE2, 0xE3):
        return Disassembly(pc, 1, opcode, f"MOVX A,@R{opcode & 0x01}")
    if opcode == 0xE4:
        return Disassembly(pc, 1, opcode, "CLR A")
    if opcode == 0xE5:
        return Disassembly(pc, 2, opcode, f"MOV A,{_format_direct(byte(1))}")
    if opcode in (0xE6, 0xE7):
        return Disassembly(pc, 1, opcode, f"MOV A,@R{opcode & 0x01}")
    if 0xE8 <= opcode <= 0xEF:
        return Disassembly(pc, 1, opcode, f"MOV A,R{opcode & 0x07}")
    if opcode == 0xF0:
        return Disassembly(pc, 1, opcode, "MOVX @DPTR,A")
    if opcode in (0xF2, 0xF3):
        return Disassembly(pc, 1, opcode, f"MOVX @R{opcode & 0x01},A")
    if opcode == 0xF4:
        return Disassembly(pc, 1, opcode, "CPL A")
    if opcode == 0xF5:
        return Disassembly(pc, 2, opcode, f"MOV {_format_direct(byte(1))},A")
    if opcode in (0xF6, 0xF7):
        return Disassembly(pc, 1, opcode, f"MOV @R{opcode & 0x01},A")
    if 0xF8 <= opcode <= 0xFF:
        return Disassembly(pc, 1, opcode, f"MOV R{opcode & 0x07},A")

    mnemonic = _ACC_SOURCE_MNEMONICS.get(opcode & 0xF0)
    if mnemonic:
        operand, size = _format_acc_source(opcode & 0x0F, byte)
        return Disassembly(pc, size, opcode, f"{mnemonic} A,{operand}")

    return Disassembly(pc, 1, opcode, f"DB 0x{opcode:02X}")


def _symbols_by_address(symbols: dict[str, int] | None) -> dict[int, list[str]]:
    by_address: dict[int, list[str]] = {}
    for name, value in (symbols or {}).items():
        by_address.setdefault(value & 0xFFFF, []).append(str(name))
    for names in by_address.values():
        names.sort()
    return by_address


def _label_for_code(address: int, symbols_by_address: dict[int, list[str]]) -> str | None:
    names = symbols_by_address.get(address & 0xFFFF)
    return names[0] if names else None


def _format_code_target(address: int, symbols_by_address: dict[int, list[str]]) -> str:
    label = _label_for_code(address, symbols_by_address)
    return label if label else f"0x{address & 0xFFFF:04X}"


def _format_immediate(value: int) -> str:
    return f"0x{value & 0xFF:02X}"


def _format_direct(address: int) -> str:
    masked = address & 0xFF
    return _DIRECT_NAMES.get(masked, f"0x{masked:02X}")


def _format_bit(bit_address: int) -> str:
    masked = bit_address & 0xFF
    exact = _BIT_NAMES.get(masked)
    if exact is not None:
        return exact
    if masked < 0x80:
        byte_address = 0x20 + (masked >> 3)
        return f"0x{byte_address:02X}.{masked & 0x07}"
    base = masked & 0xF8
    base_name = _BIT_BASE_NAMES.get(base)
    if base_name is not None:
        return f"{base_name}.{masked & 0x07}"
    return f"0x{masked:02X}"


def _format_acc_source(
    low_nibble: int,
    read_byte,
) -> tuple[str, int]:
    if low_nibble == 0x04:
        return f"#{_format_immediate(read_byte(1))}", 2
    if low_nibble == 0x05:
        return _format_direct(read_byte(1)), 2
    if low_nibble in (0x06, 0x07):
        return f"@R{low_nibble & 0x01}", 1
    if 0x08 <= low_nibble <= 0x0F:
        return f"R{low_nibble & 0x07}", 1
    return f"0x{read_byte(0):02X}", 1
