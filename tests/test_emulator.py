from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from mcs51 import MCS51, assemble_file, assemble_source, load_program, load_runtime_events, make_runtime_hook
from mcs51.cli import main as cli_main
from mcs51.debug import (
    WatchpointMonitor,
    load_symbol_file,
    make_breakpoint_hook,
    merge_symbol_info,
    resolve_breakpoints,
    resolve_watchpoints,
)
from mcs51.disasm import disassemble_instruction
from mcs51.gui import BuildCommandConfig, RunCommandConfig, build_build_argv, build_run_argv


SAMPLE_BYTES = bytes(
    [
        0x90,
        0x00,
        0x10,
        0x78,
        0x00,
        0xE8,
        0x93,
        0x60,
        0x05,
        0xF5,
        0x99,
        0x08,
        0x80,
        0xF7,
        0x80,
        0xFE,
        0x48,
        0x45,
        0x4C,
        0x4C,
        0x4F,
        0x20,
        0x35,
        0x31,
        0x00,
    ]
)


class EmulatorTests(unittest.TestCase):
    def test_asm_loader_and_execution(self) -> None:
        program = load_program(Path("examples/hello_uart.asm"))
        cpu = MCS51(program.code_memory, entry_point=program.entry_point)
        result = cpu.run(max_instructions=1000)

        self.assertEqual(program.format, "asm")
        self.assertEqual(result.serial_output, b"HELLO 51")
        self.assertEqual(result.halt_reason, "tight_loop")

    def test_hex_loader_and_execution(self) -> None:
        program = load_program(Path("examples/hello_uart.hex"))
        cpu = MCS51(program.code_memory, entry_point=program.entry_point)
        result = cpu.run(max_instructions=1000)

        self.assertEqual(result.serial_output, b"HELLO 51")
        self.assertEqual(result.halt_reason, "tight_loop")

    def test_bin_loader_and_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hello.bin"
            path.write_bytes(SAMPLE_BYTES)

            program = load_program(path, fmt="bin", origin=0)
            cpu = MCS51(program.code_memory, entry_point=program.entry_point)
            result = cpu.run(max_instructions=1000)

        self.assertEqual(result.serial_output, b"HELLO 51")
        self.assertEqual(cpu.pc, 0x000E)

    def test_disassembler_formats_raw_binary_without_symbols(self) -> None:
        disasm = disassemble_instruction(SAMPLE_BYTES, 0)
        self.assertEqual(disasm.text, "MOV DPTR,#0x0010")
        self.assertEqual(disasm.size, 3)

    def test_add_and_subtract(self) -> None:
        program = bytearray(0x10000)
        program[:8] = bytes(
            [
                0x74,
                0x05,
                0x24,
                0x03,
                0x94,
                0x01,
                0x80,
                0xFE,
            ]
        )

        cpu = MCS51(bytes(program))
        result = cpu.run(max_instructions=16)

        self.assertEqual(cpu.acc, 0x07)
        self.assertEqual(result.halt_reason, "tight_loop")

    def test_lcall_ret_and_mov_direct_direct(self) -> None:
        program = bytearray(0x10000)
        program[:13] = bytes(
            [
                0x75,
                0x20,
                0x12,
                0x12,
                0x00,
                0x09,
                0xE5,
                0x21,
                0x80,
                0xFE,
                0x85,
                0x20,
                0x21,
            ]
        )
        program[13] = 0x22

        cpu = MCS51(bytes(program))
        result = cpu.run(max_instructions=32)

        self.assertEqual(cpu.acc, 0x12)
        self.assertEqual(cpu.read_direct(0x21), 0x12)
        self.assertEqual(result.halt_reason, "tight_loop")

    def test_uart_transmit_sets_ti_for_polling_code(self) -> None:
        program = bytearray(0x10000)
        program[:8] = bytes(
            [
                0x75,
                0x99,
                0x41,
                0x30,
                0x99,
                0xFD,
                0x80,
                0xFE,
            ]
        )

        cpu = MCS51(bytes(program))
        result = cpu.run(max_instructions=32)

        self.assertEqual(result.serial_output, b"A")
        self.assertEqual(cpu.pc, 0x0006)
        self.assertEqual(result.halt_reason, "tight_loop")

    def test_serial_receive_queue_unblocks_polling_loop(self) -> None:
        program = bytearray(0x10000)
        program[:14] = bytes(
            [
                0x75,
                0x98,
                0x10,
                0x30,
                0x98,
                0xFD,
                0xE5,
                0x99,
                0xF5,
                0x20,
                0xC2,
                0x98,
                0x80,
                0xFE,
            ]
        )

        cpu = MCS51(bytes(program), serial_input=b"Z")
        result = cpu.run(max_instructions=32)

        self.assertEqual(cpu.read_direct(0x20), ord("Z"))
        self.assertEqual(result.halt_reason, "tight_loop")
        self.assertEqual(cpu.read_direct(0x98) & 0x01, 0)

    def test_timer0_interrupt_and_reti_flow(self) -> None:
        program = bytearray(0x10000)
        program[:3] = bytes([0x02, 0x00, 0x20])
        program[0x000B:0x0011] = bytes(
            [
                0x75,
                0x99,
                ord("T"),
                0xC2,
                0x8C,
                0x32,
            ]
        )
        program[0x0020:0x0030] = bytes(
            [
                0x75,
                0x89,
                0x01,
                0x75,
                0x8C,
                0xFF,
                0x75,
                0x8A,
                0xFE,
                0x75,
                0xA8,
                0x82,
                0xD2,
                0x8C,
                0x80,
                0xFE,
            ]
        )

        cpu = MCS51(bytes(program))
        result = cpu.run(max_instructions=64)

        self.assertEqual(result.serial_output, b"T")
        self.assertEqual(result.halt_reason, "tight_loop")
        self.assertTrue(result.interrupt_log)
        self.assertEqual(result.interrupt_log[0][1], "T0")
        self.assertEqual(cpu.pc, 0x002E)
        self.assertEqual(cpu.read_direct(0x88) & 0x30, 0)

    def test_generated_echo_timer_demo_end_to_end(self) -> None:
        program = load_program(Path("examples/echo_timer_demo.hex"))
        cpu = MCS51(program.code_memory, entry_point=program.entry_point, serial_input=b"AB\r")
        result = cpu.run(max_instructions=512)

        self.assertEqual(result.serial_output, b"READY>\r\nAB\r\n")
        self.assertEqual(result.halt_reason, "max_instructions")
        self.assertGreaterEqual(cpu.read_direct(0x30), 1)
        self.assertTrue(result.port_log)
        self.assertTrue(result.interrupt_log)

    def test_assemble_file_for_echo_timer_demo(self) -> None:
        result = assemble_file(Path("examples/echo_timer_demo.asm"))
        cpu = MCS51(result.code_memory, entry_point=result.entry_point, serial_input=b"AB\r")
        execution = cpu.run(max_instructions=512)

        self.assertEqual(execution.serial_output, b"READY>\r\nAB\r\n")
        self.assertEqual(result.low_address, 0x0000)
        self.assertGreaterEqual(result.high_address, 0x0080)

    def test_extended_assembler_supports_ajmp_and_acall(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    AJMP start

ORG 0030H
start:
    ACALL subr
    SJMP $

subr:
    MOV 20H,#0AAH
    RET
END
""".strip()
        )
        cpu = MCS51(result.code_memory, entry_point=result.entry_point)
        execution = cpu.run(max_instructions=32)

        self.assertEqual(cpu.read_direct(0x20), 0xAA)
        self.assertEqual(execution.halt_reason, "tight_loop")

    def test_extended_assembler_supports_moves_logic_and_movx(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    MOV 30H,#11H
    MOV R0,#30H
    MOV @R0,#11H
    MOV A,@R0
    ADD A,#05H
    MOV R1,A
    MOV 31H,R1
    MOV A,R1
    ORL A,#80H
    ANL A,#97H
    XRL A,31H
    MOV DPTR,#0123H
    MOVX @DPTR,A
    MOV A,#00H
    MOVX A,@DPTR
    XCH A,31H
    XCHD A,@R0
    SJMP $
END
""".strip()
        )
        cpu = MCS51(result.code_memory, entry_point=result.entry_point)
        execution = cpu.run(max_instructions=64)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertEqual(cpu.acc, 0x11)
        self.assertEqual(cpu.read_direct(0x30), 0x16)
        self.assertEqual(cpu.read_direct(0x31), 0x80)
        self.assertEqual(cpu.xram[0x0123], 0x80)

    def test_extended_assembler_supports_bit_branches_and_cjne_forms(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    CLR C
    JNC no_carry
    MOV 40H,#01H
no_carry:
    SETB TR0
    MOV C,TR0
    JC carry_set
    MOV 40H,#02H
carry_set:
    JBC TR0,cleared
    MOV 40H,#03H
cleared:
    ORL C,TR0
    ANL C,/P1.0
    MOV 41H,#11H
    JB TR0,still_set
    DJNZ 41H,still_set
    MOV 42H,#22H
still_set:
    MOV R2,#02H
reg_loop:
    DJNZ R2,reg_loop
    MOV A,#05H
    CJNE A,#06H,not_equal
    MOV 43H,#33H
not_equal:
    MOV A,#07H
    MOV 44H,#07H
    CJNE A,44H,direct_ne
    MOV 45H,#55H
direct_ne:
    MOV R0,#30H
    MOV @R0,#08H
    CJNE @R0,#07H,indirect_ne
    MOV 46H,#66H
indirect_ne:
    MOV R3,#09H
    CJNE R3,#08H,reg_ne
    MOV 47H,#77H
reg_ne:
    SJMP $
END
""".strip()
        )
        cpu = MCS51(result.code_memory, entry_point=result.entry_point)
        execution = cpu.run(max_instructions=128)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertEqual(cpu.read_direct(0x40), 0x00)
        self.assertEqual(cpu.read_direct(0x41), 0x10)
        self.assertEqual(cpu.read_direct(0x42), 0x00)
        self.assertEqual(cpu.read_direct(0x45), 0x55)
        self.assertEqual(cpu.read_direct(0x46), 0x00)
        self.assertEqual(cpu.read_direct(0x47), 0x00)
        self.assertEqual(cpu._read_bit(0x8C), 0)
        self.assertEqual(cpu._get_flag(cpu.CY_MASK), 0)

    def test_assembler_supports_include_for_multi_file_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "defs.inc").write_text("VALUE EQU 34H\n", encoding="utf-8")
            (temp_path / "subr.inc").write_text(
                """
helper:
    MOV 20H,#VALUE
    RET
""".strip()
                + "\n",
                encoding="utf-8",
            )
            main_path = temp_path / "main.asm"
            main_path.write_text(
                """
INCLUDE "defs.inc"
ORG 0000H
    LJMP start
INCLUDE "subr.inc"
start:
    LCALL helper
    SJMP $
END
""".strip()
                + "\n",
                encoding="utf-8",
            )

            result = assemble_file(main_path)
            cpu = MCS51(result.code_memory, entry_point=result.entry_point)
            execution = cpu.run(max_instructions=32)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertEqual(cpu.read_direct(0x20), 0x34)
        self.assertIn("HELPER", result.symbols)

    def test_assembler_supports_macros_defined_in_include(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "macros.inc").write_text(
                """
SEND_CHAR MACRO value
    MOV A,#value
    MOV SBUF,A
ENDM
""".strip()
                + "\n",
                encoding="utf-8",
            )
            main_path = temp_path / "macro_main.asm"
            main_path.write_text(
                """
INCLUDE "macros.inc"
ORG 0000H
start: SEND_CHAR 'Z'
    SJMP $
END
""".strip()
                + "\n",
                encoding="utf-8",
            )

            result = assemble_file(main_path)
            cpu = MCS51(result.code_memory, entry_point=result.entry_point)
            execution = cpu.run(max_instructions=32)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertEqual(execution.serial_output, b"Z")
        self.assertEqual(result.symbols["START"], 0x0000)

    def test_runtime_hook_injects_serial_events(self) -> None:
        program = load_program(Path("examples/echo_timer_demo.asm"))
        runtime_hook = make_runtime_hook(load_runtime_events(Path("examples/echo_timer_demo.runtime.json")))
        cpu = MCS51(program.code_memory, entry_point=program.entry_point)
        result = cpu.run(max_instructions=512, before_step=runtime_hook)

        self.assertEqual(result.serial_output, b"READY>\r\nAB\r\n")
        self.assertEqual(result.halt_reason, "max_instructions")

    def test_external_interrupt_edge_mode_requires_falling_edge(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    LJMP start

ORG 0003H
    INC 20H
    RETI

ORG 0020H
start:
    SETB IT0
    MOV IE,#81H
main_loop:
    SJMP main_loop
END
""".strip()
        )
        cpu = MCS51(result.code_memory, entry_point=result.entry_point)

        def before_step(target: MCS51, tick: int) -> None:
            if tick == 1:
                target.set_external_interrupt_line(0, 0)
            elif tick == 2:
                target.set_external_interrupt_line(0, 1)
            elif tick == 4:
                target.set_external_interrupt_line(0, 0)
            elif tick == 5:
                target.set_external_interrupt_line(0, 1)

        execution = cpu.run(max_instructions=48, before_step=before_step)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertEqual(cpu.read_direct(0x20), 2)
        self.assertEqual([entry[1] for entry in execution.interrupt_log], ["EX0", "EX0"])

    def test_external_interrupt_level_mode_retriggers_while_line_is_low(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    LJMP start

ORG 0003H
    INC 21H
    RETI

ORG 0020H
start:
    MOV IE,#81H
main_loop:
    SJMP main_loop
END
""".strip()
        )
        cpu = MCS51(result.code_memory, entry_point=result.entry_point)

        def before_step(target: MCS51, tick: int) -> None:
            if tick == 1:
                target.set_external_interrupt_line(0, 0)
            elif tick == 8:
                target.set_external_interrupt_line(0, 1)

        execution = cpu.run(max_instructions=64, before_step=before_step)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertGreaterEqual(cpu.read_direct(0x21), 2)
        self.assertGreaterEqual(len(execution.interrupt_log), 2)
        self.assertTrue(all(entry[1] == "EX0" for entry in execution.interrupt_log))

    def test_runtime_hook_can_drive_external_interrupt_lines(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    LJMP start

ORG 0003H
    INC 22H
    RETI

ORG 0020H
start:
    SETB IT0
    MOV IE,#81H
main_loop:
    SJMP main_loop
END
""".strip()
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "extint.runtime.json"
            runtime_path.write_text(
                json.dumps(
                    {
                        "events": [
                            {"tick": 1, "type": "extint0_low"},
                            {"tick": 2, "type": "extint0_high"},
                            {"tick": 4, "type": "extint0"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            runtime_hook = make_runtime_hook(load_runtime_events(runtime_path))
            cpu = MCS51(result.code_memory, entry_point=result.entry_point)
            execution = cpu.run(max_instructions=48, before_step=runtime_hook)

        self.assertEqual(execution.halt_reason, "tight_loop")
        self.assertEqual(cpu.read_direct(0x22), 2)
        self.assertEqual([entry[1] for entry in execution.interrupt_log], ["EX0", "EX0"])

    def test_timer0_mode3_split_generates_t0_and_t1_interrupts(self) -> None:
        result = assemble_source(
            """
ORG 0000H
    LJMP start

ORG 000BH
    INC 30H
    RETI

ORG 001BH
    INC 31H
    RETI

ORG 0030H
start:
    MOV TL1,#11H
    MOV TH1,#22H
    MOV TMOD,#03H
    MOV TL0,#0FEH
    MOV TH0,#0FDH
    MOV IE,#8AH
    SETB TR0
    SETB TR1
main_loop:
    SJMP main_loop
END
""".strip()
        )
        cpu = MCS51(result.code_memory, entry_point=result.entry_point)
        execution = cpu.run(max_instructions=128)

        self.assertEqual(execution.halt_reason, "max_instructions")
        self.assertGreaterEqual(cpu.read_direct(0x30), 1)
        self.assertGreaterEqual(cpu.read_direct(0x31), 1)
        self.assertEqual(cpu.read_direct(0x8B), 0x11)
        self.assertEqual(cpu.read_direct(0x8D), 0x22)
        interrupt_names = [entry[1] for entry in execution.interrupt_log]
        self.assertIn("T0", interrupt_names)
        self.assertIn("T1", interrupt_names)

    def test_breakpoint_hook_resolves_symbol(self) -> None:
        program = load_program(Path("examples/echo_timer_demo.hex"))
        symbol_file = load_symbol_file(Path("examples/echo_timer_demo.sym.json"))
        symbols, _ = merge_symbol_info(program.symbols, program.source_map, symbol_file)
        breakpoints = resolve_breakpoints(["main_loop"], symbols)
        cpu = MCS51(program.code_memory, entry_point=program.entry_point)
        result = cpu.run(max_instructions=512, before_step=make_breakpoint_hook(breakpoints))

        self.assertEqual(result.halt_reason, "breakpoint:main_loop@0x0047")
        self.assertEqual(cpu.pc, 0x0047)

    def test_watchpoint_monitor_stops_on_direct_change(self) -> None:
        program = load_program(Path("examples/echo_timer_demo.asm"))
        symbol_file = load_symbol_file(Path("examples/echo_timer_demo.sym.json"))
        symbols, _ = merge_symbol_info(program.symbols, program.source_map, symbol_file)
        monitor = WatchpointMonitor(resolve_watchpoints(["30H"], symbols))
        cpu = MCS51(program.code_memory, entry_point=program.entry_point)
        monitor.prime(cpu)
        result = cpu.run(max_instructions=128, after_step=monitor.callback)

        self.assertEqual(result.halt_reason, "watch:30H=0x01")
        self.assertEqual(cpu.read_direct(0x30), 0x01)

    def test_watchpoint_monitor_logs_bit_edges_without_halting(self) -> None:
        program = bytearray(0x10000)
        program[:6] = bytes([0xD2, 0x8C, 0xC2, 0x8C, 0x80, 0xFE])
        monitor = WatchpointMonitor(resolve_watchpoints(["log:rise:TR0", "log:fall:TR0"]))
        cpu = MCS51(bytes(program))
        monitor.prime(cpu)
        result = cpu.run(max_instructions=16, after_step=monitor.callback)

        self.assertEqual(result.halt_reason, "tight_loop")
        self.assertEqual(len(monitor.events), 2)
        self.assertEqual([event.target.trigger for event in monitor.events], ["rise", "fall"])
        self.assertEqual([event.current_value for event in monitor.events], [1, 0])

    def test_cli_project_run_creates_trace_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            project_path = temp_path / "demo.project.json"
            trace_path = temp_path / "demo.trace.txt"
            project_path.write_text(
                json.dumps(
                    {
                        "image": str(Path("examples/echo_timer_demo.hex").resolve()),
                        "symbols": str(Path("examples/echo_timer_demo.sym.json").resolve()),
                        "runtime": str(Path("examples/echo_timer_demo.runtime.json").resolve()),
                        "max_instructions": 128,
                        "watchpoints": ["log:30H"],
                        "watch_log": True,
                        "trace_file": str(trace_path),
                        "trace_limit": 6,
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = cli_main(["--project", str(project_path)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(trace_path.exists())
            self.assertIn("Trace file:", stdout.getvalue())
            self.assertIn("Watch log:", stdout.getvalue())
            self.assertIn("LABEL=START", trace_path.read_text(encoding="utf-8"))
            self.assertIn("ASM=LJMP START", trace_path.read_text(encoding="utf-8"))

    def test_cli_step_inspect_and_dump_direct(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_main(
                [
                    "examples/echo_timer_demo.asm",
                    "--step",
                    "6",
                    "--inspect",
                    "TMOD",
                    "--inspect",
                    "TR0",
                    "--dump-direct",
                    "0x88:2",
                ]
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Halt reason: step_limit:6", output)
        self.assertIn("TMOD = 0x02", output)
        self.assertIn("TR0 = 1", output)
        self.assertIn("DIRECT 0x0088:", output)

    def test_cli_watch_log_and_disassembly_for_raw_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_path = temp_path / "hello.bin"
            trace_path = temp_path / "hello.trace.txt"
            bin_path.write_bytes(SAMPLE_BYTES)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        str(bin_path),
                        "--format",
                        "bin",
                        "--origin",
                        "0",
                        "--watch",
                        "log:rise:TI",
                        "--watch-log",
                        "--trace-file",
                        str(trace_path),
                        "--trace-limit",
                        "3",
                        "--step",
                        "12",
                    ]
                )

            output = stdout.getvalue()
            trace_output = trace_path.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn("Watch log:", output)
            self.assertIn("TI rose 0 -> 1", output)
            self.assertIn("ASM=MOV DPTR,#0x0010", trace_output)
            self.assertIn("ASM=MOV R0,#0x00", trace_output)

    def test_gui_run_command_builder_covers_common_flags(self) -> None:
        argv = build_run_argv(
            RunCommandConfig(
                project="demo.project.json",
                image="demo.hex",
                fmt="hex",
                origin="0x0000",
                entry="0x0100",
                max_instructions="128",
                serial_input="AB\r",
                runtime="demo.runtime.json",
                symbols="demo.sym.json",
                breakpoints=("main_loop", "0x0047"),
                watchpoints=("30H", "log:rise:TR0"),
                inspect=("TMOD", "TR0"),
                step="6",
                dump_direct=("0x88:2",),
                dump_xram=("0x1000:4",),
                trace_file="trace.txt",
                trace_limit="16",
                watch_log=True,
                trace_ports=True,
                trace_interrupts=True,
                trace_i2c=True,
                trace_spi=True,
                dump_iram=True,
                dump_sfr=True,
                tight_loop_detect=False,
            )
        )

        self.assertEqual(
            argv,
            [
                "--project",
                "demo.project.json",
                "demo.hex",
                "--format",
                "hex",
                "--origin",
                "0x0000",
                "--entry",
                "0x0100",
                "--max-instructions",
                "128",
                "--serial-input",
                "AB\r",
                "--runtime",
                "demo.runtime.json",
                "--symbols",
                "demo.sym.json",
                "--breakpoint",
                "main_loop",
                "--breakpoint",
                "0x0047",
                "--watch",
                "30H",
                "--watch",
                "log:rise:TR0",
                "--watch-log",
                "--inspect",
                "TMOD",
                "--inspect",
                "TR0",
                "--step",
                "6",
                "--dump-direct",
                "0x88:2",
                "--dump-xram",
                "0x1000:4",
                "--trace-file",
                "trace.txt",
                "--trace-limit",
                "16",
                "--trace-ports",
                "--trace-interrupts",
                "--trace-i2c",
                "--trace-spi",
                "--dump-iram",
                "--dump-sfr",
                "--no-tight-loop-detect",
            ],
        )

    def test_gui_build_command_builder_uses_optional_outputs(self) -> None:
        argv = build_build_argv(
            BuildCommandConfig(
                source="demo.asm",
                bin_out="demo.bin",
                hex_out="demo.hex",
                sym_out="demo.sym.json",
            )
        )

        self.assertEqual(
            argv,
            [
                "demo.asm",
                "--bin-out",
                "demo.bin",
                "--hex-out",
                "demo.hex",
                "--sym-out",
                "demo.sym.json",
            ],
        )

    def test_i2c_state_machine_detects_write_transaction(self) -> None:
        """I2CBus 能正确解析主机写事务：START → addr(W) → data → STOP。"""
        from mcs51.peripheral import I2CBus

        bus = I2CBus()

        def write_port(scl: int, sda: int, tick: int) -> None:
            val = (scl << 6) | (sda << 7) | 0x3F  # 其余位为 1
            bus.on_port_write(0xB0, 0xFF, val, tick)

        # 初始状态：SCL=1, SDA=1
        write_port(1, 1, 0)

        # START: SCL=1, SDA 下降沿
        write_port(1, 0, 1)

        # 发送地址字节 0x4E (0x27 << 1 | 0 = write)
        # MSB first: 0,1,0,0,1,1,1,0
        for bit in [0, 1, 0, 0, 1, 1, 1, 0]:
            write_port(0, bit, 2)
            write_port(1, bit, 3)  # 上升沿采样

        # ACK 时钟
        write_port(0, 1, 4)
        write_port(1, 1, 5)

        # 发送数据字节 0xAB: 1,0,1,0,1,0,1,1
        for bit in [1, 0, 1, 0, 1, 0, 1, 1]:
            write_port(0, bit, 6)
            write_port(1, bit, 7)

        # ACK 时钟
        write_port(0, 1, 8)
        write_port(1, 1, 9)

        # STOP: SCL=1, SDA 上升沿
        write_port(1, 0, 10)
        write_port(1, 1, 11)

        descriptions = [desc for _, desc in bus.log]
        self.assertIn("START", descriptions)
        self.assertIn("W:0x27", descriptions)
        self.assertIn("W_DATA:0xAB", descriptions)
        self.assertIn("STOP", descriptions)

    def test_i2c_state_machine_injects_read_response(self) -> None:
        """I2CBus 能在读事务中将注入字节输出到 SDA，并记录 R_DATA。"""
        from mcs51.peripheral import I2CBus

        bus = I2CBus()
        bus.inject_response(bytes([0x55]))

        def write_port(scl: int, sda: int, tick: int) -> None:
            val = (scl << 6) | (sda << 7) | 0x3F
            bus.on_port_write(0xB0, 0xFF, val, tick)

        # START
        write_port(1, 1, 0)
        write_port(1, 0, 1)

        # 地址 0x4F (0x27 << 1 | 1 = read): 0,1,0,0,1,1,1,1
        for bit in [0, 1, 0, 0, 1, 1, 1, 1]:
            write_port(0, bit, 2)
            write_port(1, bit, 3)

        # ACK 时钟
        write_port(0, 1, 4)
        write_port(1, 1, 5)

        # 读取 8 位数据（主机 SETB SDA，脉冲 SCL，读 SDA）
        received = 0
        for i in range(8):
            write_port(0, 1, 6)   # SCL 下降沿
            sda_out = bus.get_sda_output()
            received = (received << 1) | sda_out
            write_port(1, 1, 7)   # SCL 上升沿（主机采样）

        # STOP
        write_port(0, 1, 8)
        write_port(1, 0, 9)
        write_port(1, 1, 10)

        self.assertEqual(received, 0x55)
        descriptions = [desc for _, desc in bus.log]
        self.assertIn("R:0x27", descriptions)
        self.assertIn("R_DATA:0x55", descriptions)

    def test_i2c_runtime_event_end_to_end(self) -> None:
        """通过 project 文件运行 i2c_master.asm，验证串口输出 0x55。"""
        import io
        import contextlib
        from mcs51.cli import main as cli_main

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_main(["--project", "examples/i2c_master.project.json"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("U", output)  # 0x55 = 'U'
        self.assertIn("W:0x27", output)
        self.assertIn("R_DATA:0x55", output)

    def test_spi_state_machine_detects_transfer(self) -> None:
        """SPIBus 能正确解析一次完整的 SPI 传输并注入 MISO 字节。"""
        from mcs51.peripheral import SPIBus

        bus = SPIBus()
        bus.inject_response(bytes([0x55]))

        def write_port(cs: int, sck: int, mosi: int, tick: int) -> None:
            val = (cs << 7) | (sck << 4) | (mosi << 5) | 0x40  # MISO bit 留高
            bus.on_port_write(0x90, 0xFF, val, tick)

        # CS 下降沿：帧开始
        write_port(1, 0, 1, 0)
        write_port(0, 0, 1, 1)

        # 发送 0xAB = 1,0,1,0,1,0,1,1，同时读取 MISO
        tx_bits = [1, 0, 1, 0, 1, 0, 1, 1]
        received = 0
        for bit in tx_bits:
            write_port(0, 0, bit, 2)   # SCK 低，准备 MOSI
            miso = bus.inject_miso_into_port(0xFF)
            miso_bit = (miso >> 6) & 1
            received = (received << 1) | miso_bit
            write_port(0, 1, bit, 3)   # SCK 上升沿
            write_port(0, 0, bit, 4)   # SCK 下降沿

        # CS 上升沿：帧结束
        write_port(1, 0, 1, 5)

        self.assertEqual(received, 0x55)
        self.assertEqual(len(bus.log), 1)
        self.assertIn("TX=0xAB", bus.log[0][1])
        self.assertIn("RX=0x55", bus.log[0][1])

    def test_spi_runtime_event_end_to_end(self) -> None:
        """通过 project 文件运行 spi_master.asm，验证串口输出 0x55。"""
        import io
        import contextlib
        from mcs51.cli import main as cli_main

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_main(["--project", "examples/spi_master.project.json"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("U", output)  # 0x55 = 'U'
        self.assertIn("TX=0xAB", output)
        self.assertIn("RX=0x55", output)


if __name__ == "__main__":
    unittest.main()
