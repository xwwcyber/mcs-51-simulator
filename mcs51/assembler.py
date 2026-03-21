from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


class AssemblyError(ValueError):
    """Raised when an assembly source file cannot be parsed or encoded."""


@dataclass(frozen=True)
class SourceLocation:
    address: int
    line_number: int
    text: str


@dataclass(frozen=True)
class AssemblyResult:
    code_memory: bytes
    entry_point: int
    low_address: int
    high_address: int
    source_name: str
    symbols: dict[str, int]
    source_map: dict[int, SourceLocation]


SFR_SYMBOLS = {
    "P0": 0x80,
    "SP": 0x81,
    "DPL": 0x82,
    "DPH": 0x83,
    "PCON": 0x87,
    "TCON": 0x88,
    "TMOD": 0x89,
    "TL0": 0x8A,
    "TL1": 0x8B,
    "TH0": 0x8C,
    "TH1": 0x8D,
    "P1": 0x90,
    "SCON": 0x98,
    "SBUF": 0x99,
    "P2": 0xA0,
    "IE": 0xA8,
    "P3": 0xB0,
    "IP": 0xB8,
    "PSW": 0xD0,
    "ACC": 0xE0,
    "A": 0xE0,
    "B": 0xF0,
}

BIT_SYMBOLS = {
    "IT0": 0x88,
    "IE0": 0x89,
    "IT1": 0x8A,
    "IE1": 0x8B,
    "TR0": 0x8C,
    "TF0": 0x8D,
    "TR1": 0x8E,
    "TF1": 0x8F,
    "RI": 0x98,
    "TI": 0x99,
    "RB8": 0x9A,
    "TB8": 0x9B,
    "REN": 0x9C,
    "SM2": 0x9D,
    "SM1": 0x9E,
    "SM0": 0x9F,
    "EX0": 0xA8,
    "ET0": 0xA9,
    "EX1": 0xAA,
    "ET1": 0xAB,
    "ES": 0xAC,
    "EA": 0xAF,
    "PX0": 0xB8,
    "PT0": 0xB9,
    "PX1": 0xBA,
    "PT1": 0xBB,
    "PS": 0xBC,
    "P": 0xD0,
    "OV": 0xD2,
    "RS0": 0xD3,
    "RS1": 0xD4,
    "F0": 0xD5,
    "AC": 0xD6,
    "CY": 0xD7,
}

REGISTER_NAMES = {f"R{index}": index for index in range(8)}
INDIRECT_REGISTER_NAMES = {"@R0": 0, "@R1": 1}
LABEL_PATTERN = re.compile(r"^[A-Za-z_.$?][\w.$?]*$")


@dataclass(frozen=True)
class SourceLine:
    source_name: str
    line_number: int
    address: int
    kind: str
    operator: str
    operands: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class PreprocessedLine:
    source_name: str
    line_number: int
    text: str


@dataclass(frozen=True)
class MacroDefinition:
    name: str
    parameters: tuple[str, ...]
    body: tuple[PreprocessedLine, ...]
    base_dir: Path | None


def assemble_file(path: str | Path) -> AssemblyResult:
    source_path = Path(path).resolve()
    return assemble_source(
        source_path.read_text(encoding="utf-8"),
        source_name=str(source_path),
        base_dir=source_path.parent,
    )


def assemble_source(
    source: str,
    source_name: str = "<memory>",
    base_dir: str | Path | None = None,
) -> AssemblyResult:
    symbols: dict[str, int] = {**SFR_SYMBOLS, **BIT_SYMBOLS}
    user_symbols: dict[str, int] = {}
    lines: list[SourceLine] = []
    pc = 0
    first_org: int | None = None

    resolved_base_dir = Path(base_dir).resolve() if base_dir is not None else None
    preprocessed_lines = _preprocess_source(
        source,
        source_name=source_name,
        base_dir=resolved_base_dir,
        macros={},
    )

    for preprocessed in preprocessed_lines:
        line_number = preprocessed.line_number
        line_source = preprocessed.source_name
        text = preprocessed.text
        if not text:
            continue

        equ_match = re.match(r"^([A-Za-z_.$?][\w.$?]*)\s+EQU\s+(.+)$", text, flags=re.IGNORECASE)
        if equ_match:
            name, expr = equ_match.groups()
            value = _eval_expr(expr, symbols, pc)
            symbols[name.upper()] = value
            user_symbols[name.upper()] = value
            continue

        label, remainder = _extract_label(text)
        if label:
            name = label.upper()
            if name in symbols:
                raise AssemblyError(f"{line_source}:{line_number}: duplicate symbol {label}")
            symbols[name] = pc
            user_symbols[name] = pc
            text = remainder.strip()
            if not text:
                continue

        operator, operands = _split_operator(text)
        upper_operator = operator.upper()

        if upper_operator == "ORG":
            if len(operands) != 1:
                raise AssemblyError(f"{line_source}:{line_number}: ORG expects one operand")
            pc = _eval_expr(operands[0], symbols, pc)
            if first_org is None:
                first_org = pc
            lines.append(SourceLine(line_source, line_number, pc, "org", upper_operator, operands, text))
            continue

        if upper_operator == "END":
            lines.append(SourceLine(line_source, line_number, pc, "end", upper_operator, operands, text))
            break

        line = SourceLine(line_source, line_number, pc, "stmt", upper_operator, operands, text)
        lines.append(line)
        pc += _statement_size(line, line_source)

    image = bytearray(0x10000)
    written = set()
    source_map: dict[int, SourceLocation] = {}
    low_address = 0xFFFF
    high_address = 0

    for line in lines:
        if line.kind in {"org", "end"}:
            continue

        encoded = _encode_statement(line, symbols, line.source_name)
        if not encoded:
            continue
        if line.address + len(encoded) > 0x10000:
            raise AssemblyError(
                f"{line.source_name}:{line.line_number}: code at 0x{line.address:04X} exceeds 64 KiB"
            )

        for offset, byte in enumerate(encoded):
            address = line.address + offset
            if address in written:
                raise AssemblyError(
                    f"{line.source_name}:{line.line_number}: overlapping output at 0x{address:04X}"
                )
            written.add(address)
            image[address] = byte

        source_map[line.address] = SourceLocation(
            address=line.address,
            line_number=line.line_number,
            text=line.text,
        )
        low_address = min(low_address, line.address)
        high_address = max(high_address, line.address + len(encoded) - 1)

    if not written:
        raise AssemblyError(f"{source_name}: assembly did not emit any code")

    entry_point = first_org if first_org is not None else low_address
    return AssemblyResult(
        code_memory=bytes(image),
        entry_point=entry_point & 0xFFFF,
        low_address=low_address,
        high_address=high_address,
        source_name=source_name,
        symbols=dict(sorted(user_symbols.items())),
        source_map=source_map,
    )


def _preprocess_source(
    source: str,
    source_name: str,
    base_dir: Path | None,
    macros: dict[str, MacroDefinition],
    include_stack: tuple[Path, ...] = (),
    expansion_stack: tuple[str, ...] = (),
) -> list[PreprocessedLine]:
    items = [
        PreprocessedLine(source_name, line_number, raw_line)
        for line_number, raw_line in enumerate(source.splitlines(), start=1)
    ]
    return _preprocess_items(
        items,
        base_dir=base_dir,
        macros=macros,
        include_stack=include_stack,
        expansion_stack=expansion_stack,
    )


def _preprocess_items(
    items: list[PreprocessedLine],
    base_dir: Path | None,
    macros: dict[str, MacroDefinition],
    include_stack: tuple[Path, ...] = (),
    expansion_stack: tuple[str, ...] = (),
) -> list[PreprocessedLine]:
    output: list[PreprocessedLine] = []
    index = 0

    while index < len(items):
        current = items[index]
        line_number = current.line_number
        source_name = current.source_name
        stripped = _strip_comment(current.text).strip()
        if not stripped:
            index += 1
            continue

        macro_match = re.match(
            r"^([A-Za-z_.$?][\w.$?]*)\s+MACRO(?:\s+(.*))?$",
            stripped,
            flags=re.IGNORECASE,
        )
        if macro_match:
            name, raw_params = macro_match.groups()
            if name.upper() in macros:
                raise AssemblyError(f"{source_name}:{line_number}: duplicate macro {name}")

            body: list[PreprocessedLine] = []
            index += 1
            while index < len(items):
                body_item = items[index]
                body_line_number = body_item.line_number
                body_text = _strip_comment(body_item.text).strip()
                if body_text:
                    if re.match(
                        r"^([A-Za-z_.$?][\w.$?]*)\s+MACRO(?:\s+(.*))?$",
                        body_text,
                        flags=re.IGNORECASE,
                    ):
                        raise AssemblyError(
                            f"{source_name}:{body_line_number}: nested MACRO definitions are not supported"
                        )
                    if body_text.upper() == "ENDM":
                        break
                    body.append(PreprocessedLine(body_item.source_name, body_line_number, body_text))
                index += 1
            else:
                raise AssemblyError(f"{source_name}:{line_number}: MACRO {name} is missing ENDM")

            macros[name.upper()] = MacroDefinition(
                name=name,
                parameters=_parse_macro_parameters(raw_params, source_name, line_number),
                body=tuple(body),
                base_dir=base_dir,
            )
            index += 1
            continue

        label, remainder = _extract_label(stripped)
        code = remainder.strip() if label else stripped
        operator = ""
        operands: tuple[str, ...] = ()
        if code:
            operator, operands = _split_operator(code)
            upper_operator = operator.upper()
            if upper_operator == "INCLUDE":
                if label:
                    raise AssemblyError(f"{source_name}:{line_number}: labels are not allowed on INCLUDE")
                include_path = _resolve_include_path(operands, source_name, line_number, base_dir)
                if include_path in include_stack:
                    chain = " -> ".join(str(path) for path in (*include_stack, include_path))
                    raise AssemblyError(f"{source_name}:{line_number}: recursive INCLUDE detected: {chain}")
                output.extend(
                    _preprocess_source(
                        include_path.read_text(encoding="utf-8"),
                        source_name=str(include_path),
                        base_dir=include_path.parent,
                        macros=macros,
                        include_stack=(*include_stack, include_path),
                        expansion_stack=expansion_stack,
                    )
                )
                index += 1
                continue
            if upper_operator == "ENDM":
                raise AssemblyError(f"{source_name}:{line_number}: ENDM without matching MACRO")

            macro = macros.get(upper_operator)
            if macro is not None:
                if macro.name.upper() in expansion_stack:
                    chain = " -> ".join((*expansion_stack, macro.name.upper()))
                    raise AssemblyError(f"{source_name}:{line_number}: recursive macro expansion detected: {chain}")
                expansion = _expand_macro(
                    macro,
                    operands,
                    call_site=PreprocessedLine(source_name, line_number, stripped),
                )
                output.extend(
                    _preprocess_items(
                        expansion,
                        base_dir=macro.base_dir,
                        macros=macros,
                        include_stack=include_stack,
                        expansion_stack=(*expansion_stack, macro.name.upper()),
                    )
                )
                index += 1
                continue

        output.append(PreprocessedLine(source_name, line_number, stripped))
        if operator.upper() == "END":
            break
        index += 1

    return output


def _parse_macro_parameters(raw_params: str | None, source_name: str, line_number: int) -> tuple[str, ...]:
    if raw_params is None or not raw_params.strip():
        return ()

    params = tuple(part.strip() for part in _split_csv(raw_params) if part.strip())
    for param in params:
        if not LABEL_PATTERN.match(param):
            raise AssemblyError(f"{source_name}:{line_number}: invalid macro parameter {param}")
    return params


def _resolve_include_path(
    operands: tuple[str, ...],
    source_name: str,
    line_number: int,
    base_dir: Path | None,
) -> Path:
    if len(operands) != 1:
        raise AssemblyError(f"{source_name}:{line_number}: INCLUDE expects exactly one path operand")

    operand = operands[0].strip()
    include_text: str
    if operand.startswith(("'", '"')):
        literal = ast.literal_eval(operand)
        if not isinstance(literal, str):
            raise AssemblyError(f"{source_name}:{line_number}: INCLUDE path must be a string")
        include_text = literal
    else:
        include_text = operand

    candidate = Path(include_text)
    if not candidate.is_absolute():
        candidate = (base_dir or Path.cwd()) / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise AssemblyError(f"{source_name}:{line_number}: INCLUDE file not found: {resolved}")
    return resolved


def _expand_macro(
    macro: MacroDefinition,
    operands: tuple[str, ...],
    call_site: PreprocessedLine,
) -> list[PreprocessedLine]:
    if len(operands) != len(macro.parameters):
        raise AssemblyError(
            f"{call_site.source_name}:{call_site.line_number}: "
            f"macro {macro.name} expects {len(macro.parameters)} operands, got {len(operands)}"
        )

    substitutions = {
        parameter.upper(): operand.strip()
        for parameter, operand in zip(macro.parameters, operands)
    }

    expanded: list[PreprocessedLine] = []
    for body_line in macro.body:
        text = body_line.text
        for parameter, operand in substitutions.items():
            pattern = re.compile(rf"(?<![\w.$?]){re.escape(parameter)}(?![\w.$?])", re.IGNORECASE)
            text = pattern.sub(lambda _match, replacement=operand: replacement, text)
        expanded.append(PreprocessedLine(body_line.source_name, body_line.line_number, text))

    label, remainder = _extract_label(call_site.text)
    if label and expanded:
        prefixed = f"{label}: {expanded[0].text}"
        expanded[0] = PreprocessedLine(expanded[0].source_name, expanded[0].line_number, prefixed)
    elif label and not expanded:
        expanded.append(PreprocessedLine(call_site.source_name, call_site.line_number, f"{label}:"))

    return expanded


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == ";" and not in_single and not in_double:
            return line[:index]
    return line


def _extract_label(text: str) -> tuple[str | None, str]:
    if ":" not in text:
        return None, text
    candidate, remainder = text.split(":", 1)
    label = candidate.strip()
    if LABEL_PATTERN.match(label):
        return label, remainder
    return None, text


def _split_operator(text: str) -> tuple[str, tuple[str, ...]]:
    parts = text.split(None, 1)
    operator = parts[0]
    operands = ()
    if len(parts) > 1:
        operands = tuple(_split_csv(parts[1]))
    return operator, operands


def _split_csv(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False

    for char in text:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "," and not in_single and not in_double:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)

    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _statement_size(line: SourceLine, source_name: str) -> int:
    op = line.operator
    operands = line.operands

    if op == "DB":
        return sum(len(_db_bytes(token, {}, line.address, allow_symbols=False)) for token in operands)

    if op in {"NOP", "RET", "RETI"}:
        _require_operands(line, 0, source_name)
        return 1
    if op in {"RR", "RL", "RRC", "RLC", "SWAP", "DA"}:
        operand = _require_operands(line, 1, source_name)[0]
        if operand.upper() != "A":
            raise AssemblyError(f"{source_name}:{line.line_number}: {op} requires operand A")
        return 1
    if op in {"MUL", "DIV"}:
        operand = _require_operands(line, 1, source_name)[0]
        if operand.upper() != "AB":
            raise AssemblyError(f"{source_name}:{line.line_number}: {op} requires operand AB")
        return 1
    if op == "JMP":
        operand = _require_operands(line, 1, source_name)[0]
        if operand.upper() != "@A+DPTR":
            raise AssemblyError(f"{source_name}:{line.line_number}: only JMP @A+DPTR is supported")
        return 1
    if op == "MOVC":
        _movc_operands(line, source_name)
        return 1
    if op == "MOVX":
        _movx_operands(line, source_name)
        return 1
    if op in {"LJMP", "LCALL"}:
        _require_operands(line, 1, source_name)
        return 3
    if op in {"AJMP", "ACALL", "SJMP", "JZ", "JNZ", "JC", "JNC"}:
        _require_operands(line, 1, source_name)
        return 2
    if op in {"JB", "JBC", "JNB"}:
        _require_operands(line, 2, source_name)
        return 3
    if op == "CJNE":
        return _cjne_size(line, source_name)
    if op == "DJNZ":
        return _djnz_size(line, source_name)
    if op in {"INC", "DEC"}:
        if len(operands) != 1:
            raise AssemblyError(f"{source_name}:{line.line_number}: {op} expects one operand")
        upper = operands[0].upper()
        if upper in {"A", "DPTR"} or upper in REGISTER_NAMES or upper in INDIRECT_REGISTER_NAMES:
            return 1
        return 2
    if op in {"CLR", "SETB", "CPL"}:
        if len(operands) != 1:
            raise AssemblyError(f"{source_name}:{line.line_number}: {op} expects one operand")
        upper = operands[0].upper()
        if upper == "A" or upper == "C":
            return 1
        return 2
    if op in {"PUSH", "POP"}:
        _require_operands(line, 1, source_name)
        return 2
    if op in {"ADD", "ADDC", "SUBB"}:
        return _binary_accumulator_size(line, source_name)
    if op in {"ORL", "ANL", "XRL"}:
        return _logic_size(line, source_name)
    if op == "XCH":
        return _xch_size(line, source_name)
    if op == "XCHD":
        return _xchd_size(line, source_name)
    if op == "MOV":
        return _mov_size(line, source_name)

    raise AssemblyError(f"{source_name}:{line.line_number}: unsupported operator {op}")


def _mov_size(line: SourceLine, source_name: str) -> int:
    operands = line.operands
    if len(operands) != 2:
        raise AssemblyError(f"{source_name}:{line.line_number}: MOV expects two operands")

    dst = operands[0].upper()
    src = operands[1].upper()
    if dst == "DPTR":
        if not operands[1].startswith("#"):
            raise AssemblyError(f"{source_name}:{line.line_number}: MOV DPTR requires immediate source")
        return 3
    if dst == "A" and src in {"@A+DPTR", "@A+PC"}:
        raise AssemblyError(f"{source_name}:{line.line_number}: use MOVC {operands[0]},{operands[1]} instead")
    if dst == "C":
        _resolve_bit_operand(operands[1], allow_inverse=False)
        return 2
    if src == "C":
        _resolve_bit_operand(operands[0], allow_inverse=False)
        return 2
    if dst == "A":
        if _indirect_register_index(src) is not None or _register_index(src) is not None:
            return 1
        return 2
    if _indirect_register_index(dst) is not None:
        if src == "A":
            return 1
        if operands[1].startswith("#") or _is_direct_like(operands[1]):
            return 2
        raise AssemblyError(f"{source_name}:{line.line_number}: unsupported MOV source {operands[1]}")
    if dst in REGISTER_NAMES:
        if src == "A":
            return 1
        return 2
    if src in REGISTER_NAMES or _indirect_register_index(src) is not None:
        return 2
    if src == "A":
        return 2
    if operands[1].startswith("#"):
        return 3
    return 3


def _encode_statement(line: SourceLine, symbols: dict[str, int], source_name: str) -> bytes:
    op = line.operator
    operands = line.operands

    if op == "DB":
        data = bytearray()
        for token in operands:
            data.extend(_db_bytes(token, symbols, line.address))
        return bytes(data)

    if op == "NOP":
        return bytes([0x00])
    if op == "RET":
        return bytes([0x22])
    if op == "RETI":
        return bytes([0x32])
    if op == "RR":
        _require_acc_operand(line, source_name)
        return bytes([0x03])
    if op == "RL":
        _require_acc_operand(line, source_name)
        return bytes([0x23])
    if op == "RRC":
        _require_acc_operand(line, source_name)
        return bytes([0x13])
    if op == "RLC":
        _require_acc_operand(line, source_name)
        return bytes([0x33])
    if op == "SWAP":
        _require_acc_operand(line, source_name)
        return bytes([0xC4])
    if op == "DA":
        _require_acc_operand(line, source_name)
        return bytes([0xD4])
    if op == "MUL":
        _require_ab_operand(line, source_name)
        return bytes([0xA4])
    if op == "DIV":
        _require_ab_operand(line, source_name)
        return bytes([0x84])
    if op == "JMP":
        operand = _require_operands(line, 1, source_name)[0]
        if operand.upper() != "@A+DPTR":
            raise AssemblyError(f"{source_name}:{line.line_number}: only JMP @A+DPTR is supported")
        return bytes([0x73])
    if op == "AJMP":
        target = _eval_expr(_require_operands(line, 1, source_name)[0], symbols, line.address)
        return _encode_page_jump(0x01, target, line.address, source_name, line.line_number)
    if op == "ACALL":
        target = _eval_expr(_require_operands(line, 1, source_name)[0], symbols, line.address)
        return _encode_page_jump(0x11, target, line.address, source_name, line.line_number)
    if op == "LJMP":
        target = _eval_expr(_require_operands(line, 1, source_name)[0], symbols, line.address)
        return bytes([0x02, (target >> 8) & 0xFF, target & 0xFF])
    if op == "LCALL":
        target = _eval_expr(_require_operands(line, 1, source_name)[0], symbols, line.address)
        return bytes([0x12, (target >> 8) & 0xFF, target & 0xFF])
    if op == "SJMP":
        return bytes([0x80, _relative_byte(operands[0], symbols, line.address, 2, source_name, line.line_number)])
    if op == "JC":
        return bytes([0x40, _relative_byte(operands[0], symbols, line.address, 2, source_name, line.line_number)])
    if op == "JNC":
        return bytes([0x50, _relative_byte(operands[0], symbols, line.address, 2, source_name, line.line_number)])
    if op == "JZ":
        return bytes([0x60, _relative_byte(operands[0], symbols, line.address, 2, source_name, line.line_number)])
    if op == "JNZ":
        return bytes([0x70, _relative_byte(operands[0], symbols, line.address, 2, source_name, line.line_number)])
    if op == "JB":
        bit_address, target = _require_operands(line, 2, source_name)
        return bytes(
            [
                0x20,
                _resolve_bit(bit_address, symbols, line.address),
                _relative_byte(target, symbols, line.address, 3, source_name, line.line_number),
            ]
        )
    if op == "JBC":
        bit_address, target = _require_operands(line, 2, source_name)
        return bytes(
            [
                0x10,
                _resolve_bit(bit_address, symbols, line.address),
                _relative_byte(target, symbols, line.address, 3, source_name, line.line_number),
            ]
        )
    if op == "JNB":
        bit_address, target = _require_operands(line, 2, source_name)
        return bytes(
            [
                0x30,
                _resolve_bit(bit_address, symbols, line.address),
                _relative_byte(target, symbols, line.address, 3, source_name, line.line_number),
            ]
        )
    if op == "CJNE":
        return _encode_cjne(line, symbols, source_name)
    if op == "DJNZ":
        return _encode_djnz(line, symbols, source_name)
    if op == "MOVC":
        return _encode_movc(line, source_name)
    if op == "MOVX":
        return _encode_movx(line, source_name)
    if op in {"INC", "DEC"}:
        operand = _require_operands(line, 1, source_name)[0]
        return _encode_inc_dec(op, operand, symbols, line, source_name)
    if op in {"ADD", "ADDC", "SUBB"}:
        return _encode_binary_accumulator(op, line, symbols, source_name)
    if op in {"ORL", "ANL", "XRL"}:
        return _encode_logic(op, line, symbols, source_name)
    if op == "XCH":
        return _encode_xch(line, symbols, source_name)
    if op == "XCHD":
        return _encode_xchd(line, source_name)
    if op in {"CLR", "SETB", "CPL"}:
        operand = _require_operands(line, 1, source_name)[0]
        return _encode_bit_op(op, operand, symbols, line, source_name)
    if op == "PUSH":
        operand = _require_operands(line, 1, source_name)[0]
        return bytes([0xC0, _resolve_direct(operand, symbols, line.address)])
    if op == "POP":
        operand = _require_operands(line, 1, source_name)[0]
        return bytes([0xD0, _resolve_direct(operand, symbols, line.address)])
    if op == "MOV":
        return _encode_mov(line, symbols, source_name)

    raise AssemblyError(f"{source_name}:{line.line_number}: unsupported operator {op}")


def _encode_inc_dec(
    operator: str,
    operand: str,
    symbols: dict[str, int],
    line: SourceLine,
    source_name: str,
) -> bytes:
    token = operand.upper()
    if token == "A":
        return bytes([0x04 if operator == "INC" else 0x14])
    if token == "DPTR":
        if operator != "INC":
            raise AssemblyError(f"{source_name}:{line.line_number}: DEC DPTR is not supported")
        return bytes([0xA3])
    if token in REGISTER_NAMES:
        base = 0x08 if operator == "INC" else 0x18
        return bytes([base + REGISTER_NAMES[token]])
    indirect = _indirect_register_index(token)
    if indirect is not None:
        base = 0x06 if operator == "INC" else 0x16
        return bytes([base + indirect])
    base = 0x05 if operator == "INC" else 0x15
    return bytes([base, _resolve_direct(operand, symbols, line.address)])


def _encode_bit_op(
    operator: str,
    operand: str,
    symbols: dict[str, int],
    line: SourceLine,
    source_name: str,
) -> bytes:
    token = operand.upper()
    if operator == "CLR" and token == "A":
        return bytes([0xE4])
    if operator == "CLR" and token == "C":
        return bytes([0xC3])
    if operator == "SETB" and token == "C":
        return bytes([0xD3])
    if operator == "CPL" and token == "A":
        return bytes([0xF4])
    if operator == "CPL" and token == "C":
        return bytes([0xB3])
    opcode = {"CLR": 0xC2, "SETB": 0xD2, "CPL": 0xB2}[operator]
    return bytes([opcode, _resolve_bit(operand, symbols, line.address)])


def _encode_mov(line: SourceLine, symbols: dict[str, int], source_name: str) -> bytes:
    dst, src = line.operands
    dst_upper = dst.upper()
    src_upper = src.upper()

    if dst_upper == "DPTR" and src.startswith("#"):
        value = _eval_expr(src[1:], symbols, line.address) & 0xFFFF
        return bytes([0x90, (value >> 8) & 0xFF, value & 0xFF])
    if dst_upper == "C":
        return bytes([0xA2, _resolve_bit(src, symbols, line.address)])
    if src_upper == "C":
        return bytes([0x92, _resolve_bit(dst, symbols, line.address)])

    if dst_upper == "A":
        if src.startswith("#"):
            return bytes([0x74, _eval_expr(src[1:], symbols, line.address) & 0xFF])
        indirect = _indirect_register_index(src_upper)
        if indirect is not None:
            return bytes([0xE6 + indirect])
        register = _register_index(src_upper)
        if register is not None:
            return bytes([0xE8 + register])
        return bytes([0xE5, _resolve_direct(src, symbols, line.address)])

    if dst_upper in REGISTER_NAMES:
        index = REGISTER_NAMES[dst_upper]
        if src.startswith("#"):
            return bytes([0x78 + index, _eval_expr(src[1:], symbols, line.address) & 0xFF])
        if src_upper == "A":
            return bytes([0xF8 + index])
        return bytes([0xA8 + index, _resolve_direct(src, symbols, line.address)])

    indirect_dst = _indirect_register_index(dst_upper)
    if indirect_dst is not None:
        if src.startswith("#"):
            return bytes([0x76 + indirect_dst, _eval_expr(src[1:], symbols, line.address) & 0xFF])
        if src_upper == "A":
            return bytes([0xF6 + indirect_dst])
        return bytes([0xA6 + indirect_dst, _resolve_direct(src, symbols, line.address)])

    if src_upper == "A":
        return bytes([0xF5, _resolve_direct(dst, symbols, line.address)])

    indirect_src = _indirect_register_index(src_upper)
    if indirect_src is not None:
        return bytes([0x86 + indirect_src, _resolve_direct(dst, symbols, line.address)])
    if src_upper in REGISTER_NAMES:
        return bytes([0x88 + REGISTER_NAMES[src_upper], _resolve_direct(dst, symbols, line.address)])

    if src.startswith("#"):
        return bytes(
            [
                0x75,
                _resolve_direct(dst, symbols, line.address),
                _eval_expr(src[1:], symbols, line.address) & 0xFF,
            ]
        )

    return bytes(
        [
            0x85,
            _resolve_direct(src, symbols, line.address),
            _resolve_direct(dst, symbols, line.address),
        ]
    )


def _require_acc_operand(line: SourceLine, source_name: str) -> None:
    operand = _require_operands(line, 1, source_name)[0]
    if operand.upper() != "A":
        raise AssemblyError(f"{source_name}:{line.line_number}: {line.operator} requires operand A")


def _require_ab_operand(line: SourceLine, source_name: str) -> None:
    operand = _require_operands(line, 1, source_name)[0]
    if operand.upper() != "AB":
        raise AssemblyError(f"{source_name}:{line.line_number}: {line.operator} requires operand AB")


def _register_index(token: str) -> int | None:
    return REGISTER_NAMES.get(token.upper())


def _indirect_register_index(token: str) -> int | None:
    return INDIRECT_REGISTER_NAMES.get(token.upper())


def _is_direct_like(token: str) -> bool:
    upper = token.upper()
    return (
        not token.startswith("#")
        and upper not in {"A", "C", "DPTR", "AB", "@A+DPTR", "@A+PC", "@DPTR"}
        and _register_index(upper) is None
        and _indirect_register_index(upper) is None
    )


def _resolve_bit_operand(token: str, allow_inverse: bool = True) -> tuple[str, bool]:
    text = token.strip()
    inverse = False
    if text.startswith("/"):
        if not allow_inverse:
            raise AssemblyError(f"Inverse bit operand is not allowed here: {token}")
        inverse = True
        text = text[1:].strip()
    if not text:
        raise AssemblyError(f"Invalid bit operand: {token}")
    return text, inverse


def _encode_page_jump(
    opcode_base: int,
    target: int,
    address: int,
    source_name: str,
    line_number: int,
) -> bytes:
    next_pc = (address + 2) & 0xFFFF
    if (target & 0xF800) != (next_pc & 0xF800):
        raise AssemblyError(
            f"{source_name}:{line_number}: target 0x{target:04X} is out of AJMP/ACALL page range"
        )
    return bytes([opcode_base | ((target >> 3) & 0xE0), target & 0xFF])


def _movc_operands(line: SourceLine, source_name: str) -> tuple[str, str]:
    left, right = _require_operands(line, 2, source_name)
    if left.upper() != "A" or right.upper() not in {"@A+DPTR", "@A+PC"}:
        raise AssemblyError(
            f"{source_name}:{line.line_number}: only MOVC A,@A+DPTR and MOVC A,@A+PC are supported"
        )
    return left, right


def _encode_movc(line: SourceLine, source_name: str) -> bytes:
    _, right = _movc_operands(line, source_name)
    return bytes([0x93 if right.upper() == "@A+DPTR" else 0x83])


def _movx_operands(line: SourceLine, source_name: str) -> tuple[str, str]:
    left, right = _require_operands(line, 2, source_name)
    left_upper = left.upper()
    right_upper = right.upper()
    if left_upper == "A" and right_upper in {"@DPTR", "@R0", "@R1"}:
        return left, right
    if right_upper == "A" and left_upper in {"@DPTR", "@R0", "@R1"}:
        return left, right
    raise AssemblyError(
        f"{source_name}:{line.line_number}: supported MOVX forms are A,@DPTR/@R0/@R1 and @DPTR/@R0/@R1,A"
    )


def _encode_movx(line: SourceLine, source_name: str) -> bytes:
    left, right = _movx_operands(line, source_name)
    left_upper = left.upper()
    right_upper = right.upper()
    if left_upper == "A":
        if right_upper == "@DPTR":
            return bytes([0xE0])
        return bytes([0xE2 + _indirect_register_index(right_upper)])
    if left_upper == "@DPTR":
        return bytes([0xF0])
    return bytes([0xF2 + _indirect_register_index(left_upper)])


def _encode_acc_source(
    opcode_base: int,
    operand: str,
    symbols: dict[str, int],
    current_address: int,
) -> bytes:
    upper = operand.upper()
    if operand.startswith("#"):
        return bytes([opcode_base + 0x04, _eval_expr(operand[1:], symbols, current_address) & 0xFF])
    indirect = _indirect_register_index(upper)
    if indirect is not None:
        return bytes([opcode_base + 0x06 + indirect])
    register = _register_index(upper)
    if register is not None:
        return bytes([opcode_base + 0x08 + register])
    return bytes([opcode_base + 0x05, _resolve_direct(operand, symbols, current_address)])


def _binary_accumulator_size(line: SourceLine, source_name: str) -> int:
    left, right = _require_operands(line, 2, source_name)
    if left.upper() != "A":
        raise AssemblyError(f"{source_name}:{line.line_number}: {line.operator} requires A as destination")
    if right.startswith("#") or _is_direct_like(right):
        return 2
    if _indirect_register_index(right) is not None or _register_index(right) is not None:
        return 1
    raise AssemblyError(f"{source_name}:{line.line_number}: unsupported operand {right}")


def _encode_binary_accumulator(
    operator: str,
    line: SourceLine,
    symbols: dict[str, int],
    source_name: str,
) -> bytes:
    _binary_accumulator_size(line, source_name)
    _, right = line.operands
    opcode_base = {
        "ADD": 0x20,
        "ADDC": 0x30,
        "SUBB": 0x90,
    }[operator]
    return _encode_acc_source(opcode_base, right, symbols, line.address)


def _logic_size(line: SourceLine, source_name: str) -> int:
    left, right = _require_operands(line, 2, source_name)
    left_upper = left.upper()
    right_upper = right.upper()
    if left_upper == "A":
        return _binary_accumulator_size(line, source_name)
    if left_upper == "C":
        if line.operator == "XRL":
            raise AssemblyError(f"{source_name}:{line.line_number}: XRL does not support carry operands")
        _resolve_bit_operand(right, allow_inverse=True)
        return 2
    if right_upper == "A":
        return 2
    if right.startswith("#"):
        return 3
    raise AssemblyError(f"{source_name}:{line.line_number}: unsupported {line.operator} operand pair")


def _encode_logic(
    operator: str,
    line: SourceLine,
    symbols: dict[str, int],
    source_name: str,
) -> bytes:
    _logic_size(line, source_name)
    left, right = line.operands
    left_upper = left.upper()
    right_upper = right.upper()
    if left_upper == "A":
        opcode_base = {
            "ORL": 0x40,
            "ANL": 0x50,
            "XRL": 0x60,
        }[operator]
        return _encode_acc_source(opcode_base, right, symbols, line.address)
    if left_upper == "C":
        bit_token, inverse = _resolve_bit_operand(right, allow_inverse=True)
        if operator == "ORL":
            return bytes([0xA0 if inverse else 0x72, _resolve_bit(bit_token, symbols, line.address)])
        return bytes([0xB0 if inverse else 0x82, _resolve_bit(bit_token, symbols, line.address)])
    if right_upper == "A":
        opcode = {"ORL": 0x42, "ANL": 0x52, "XRL": 0x62}[operator]
        return bytes([opcode, _resolve_direct(left, symbols, line.address)])
    opcode = {"ORL": 0x43, "ANL": 0x53, "XRL": 0x63}[operator]
    return bytes(
        [
            opcode,
            _resolve_direct(left, symbols, line.address),
            _eval_expr(right[1:], symbols, line.address) & 0xFF,
        ]
    )


def _cjne_size(line: SourceLine, source_name: str) -> int:
    left, right, _ = _require_operands(line, 3, source_name)
    left_upper = left.upper()
    if left_upper == "A":
        if right.startswith("#") or _is_direct_like(right):
            return 3
    elif _indirect_register_index(left_upper) is not None:
        if right.startswith("#"):
            return 3
    elif _register_index(left_upper) is not None:
        if right.startswith("#"):
            return 3
    raise AssemblyError(
        f"{source_name}:{line.line_number}: supported CJNE forms are A,#imm,label; A,direct,label; @R0/@R1,#imm,label; Rn,#imm,label"
    )


def _encode_cjne(line: SourceLine, symbols: dict[str, int], source_name: str) -> bytes:
    _cjne_size(line, source_name)
    left, right, target = line.operands
    left_upper = left.upper()
    rel = _relative_byte(target, symbols, line.address, 3, source_name, line.line_number)
    if left_upper == "A" and right.startswith("#"):
        return bytes([0xB4, _eval_expr(right[1:], symbols, line.address) & 0xFF, rel])
    if left_upper == "A":
        return bytes([0xB5, _resolve_direct(right, symbols, line.address), rel])
    indirect = _indirect_register_index(left_upper)
    if indirect is not None:
        return bytes([0xB6 + indirect, _eval_expr(right[1:], symbols, line.address) & 0xFF, rel])
    register = _register_index(left_upper)
    return bytes([0xB8 + register, _eval_expr(right[1:], symbols, line.address) & 0xFF, rel])


def _djnz_size(line: SourceLine, source_name: str) -> int:
    left, _ = _require_operands(line, 2, source_name)
    if _register_index(left) is not None:
        return 2
    return 3


def _encode_djnz(line: SourceLine, symbols: dict[str, int], source_name: str) -> bytes:
    left, target = _require_operands(line, 2, source_name)
    register = _register_index(left)
    if register is not None:
        return bytes(
            [
                0xD8 + register,
                _relative_byte(target, symbols, line.address, 2, source_name, line.line_number),
            ]
        )
    return bytes(
        [
            0xD5,
            _resolve_direct(left, symbols, line.address),
            _relative_byte(target, symbols, line.address, 3, source_name, line.line_number),
        ]
    )


def _xchd_size(line: SourceLine, source_name: str) -> int:
    left, right = _require_operands(line, 2, source_name)
    if left.upper() != "A" or _indirect_register_index(right) is None:
        raise AssemblyError(f"{source_name}:{line.line_number}: XCHD only supports A,@R0 or A,@R1")
    return 1


def _xch_size(line: SourceLine, source_name: str) -> int:
    left, right = _require_operands(line, 2, source_name)
    if left.upper() != "A":
        raise AssemblyError(f"{source_name}:{line.line_number}: XCH requires A as destination")
    if _indirect_register_index(right) is not None or _register_index(right) is not None:
        return 1
    if right.startswith("#"):
        raise AssemblyError(f"{source_name}:{line.line_number}: XCH does not support immediate source")
    return 2


def _encode_xchd(line: SourceLine, source_name: str) -> bytes:
    _xchd_size(line, source_name)
    _, right = line.operands
    return bytes([0xD6 + _indirect_register_index(right)])


def _encode_xch(line: SourceLine, symbols: dict[str, int], source_name: str) -> bytes:
    _, right = _require_operands(line, 2, source_name)
    _xch_size(line, source_name)
    indirect = _indirect_register_index(right)
    if indirect is not None:
        return bytes([0xC6 + indirect])
    register = _register_index(right)
    if register is not None:
        return bytes([0xC8 + register])
    return bytes([0xC5, _resolve_direct(right, symbols, line.address)])


def _require_operands(line: SourceLine, expected: int, source_name: str) -> tuple[str, ...]:
    if len(line.operands) != expected:
        raise AssemblyError(
            f"{source_name}:{line.line_number}: {line.operator} expects {expected} operands"
        )
    return line.operands


def _relative_byte(
    expression: str,
    symbols: dict[str, int],
    address: int,
    size: int,
    source_name: str,
    line_number: int,
) -> int:
    target = _eval_expr(expression, symbols, address)
    offset = target - (address + size)
    if offset < -128 or offset > 127:
        raise AssemblyError(
            f"{source_name}:{line_number}: relative target {expression} is out of range"
        )
    return offset & 0xFF


def _resolve_direct(token: str, symbols: dict[str, int], current_address: int) -> int:
    return _eval_expr(token, symbols, current_address) & 0xFF


def _resolve_bit(token: str, symbols: dict[str, int], current_address: int) -> int:
    upper = token.upper()
    if "." in upper:
        base, bit = upper.split(".", 1)
        if base not in SFR_SYMBOLS:
            raise AssemblyError(f"Unknown bit-addressable SFR {base}")
        bit_value = _eval_expr(bit, symbols, current_address)
        if bit_value < 0 or bit_value > 7:
            raise AssemblyError(f"Bit index out of range in {token}")
        return (SFR_SYMBOLS[base] & 0xF8) | bit_value
    if upper in BIT_SYMBOLS:
        return BIT_SYMBOLS[upper]
    return _eval_expr(token, symbols, current_address) & 0xFF


def _db_bytes(
    token: str,
    symbols: dict[str, int],
    current_address: int,
    allow_symbols: bool = True,
) -> bytes:
    value = token.strip()
    if value.startswith(("'", '"')):
        literal = ast.literal_eval(value)
        if isinstance(literal, str):
            return literal.encode("latin-1")
        if isinstance(literal, bytes):
            return literal
        raise AssemblyError(f"DB literal must be a string or bytes: {token}")
    if not allow_symbols and not _looks_numeric(value):
        return b"\x00"
    return bytes([_eval_expr(value, symbols, current_address) & 0xFF])


def _looks_numeric(token: str) -> bool:
    upper = token.upper()
    return (
        upper.startswith("0X")
        or upper.endswith("H")
        or upper.endswith("B")
        or token.isdigit()
        or token.startswith(("+", "-"))
        or token == "$"
    )


def _eval_expr(expression: str, symbols: dict[str, int], current_address: int) -> int:
    text = expression.strip()
    if not text:
        raise AssemblyError("empty expression")

    total = 0
    sign = 1
    token = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char in "+-":
            if token.strip():
                total += sign * _eval_term(token.strip(), symbols, current_address)
                token = ""
            sign = 1 if char == "+" else -1
            index += 1
            continue
        token += char
        index += 1

    if token.strip():
        total += sign * _eval_term(token.strip(), symbols, current_address)
    return total


def _eval_term(term: str, symbols: dict[str, int], current_address: int) -> int:
    upper = term.upper()
    if upper == "$":
        return current_address
    if upper in symbols:
        return symbols[upper]
    if upper.startswith("0X"):
        return int(upper, 16)
    if upper.endswith("H") and upper[:-1]:
        return int(upper[:-1], 16)
    if upper.endswith("B") and upper[:-1] and set(upper[:-1]) <= {"0", "1"}:
        return int(upper[:-1], 2)
    if term.isdigit():
        return int(term, 10)
    if (term.startswith("'") and term.endswith("'")) or (term.startswith('"') and term.endswith('"')):
        literal = ast.literal_eval(term)
        if isinstance(literal, str) and len(literal) == 1:
            return ord(literal)
    raise AssemblyError(f"Unknown symbol or literal: {term}")
