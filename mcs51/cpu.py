from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable


class UnsupportedOpcodeError(RuntimeError):
    """Raised when the emulator encounters an opcode it does not support."""


@dataclass(frozen=True)
class ExecutionResult:
    instructions: int
    ticks: int
    halt_reason: str
    serial_output: bytes
    port_log: tuple[tuple[int, str, int], ...]
    interrupt_log: tuple[tuple[int, str, int], ...]


class MCS51:
    SP_ADDR = 0x81
    DPL_ADDR = 0x82
    DPH_ADDR = 0x83
    TCON_ADDR = 0x88
    TMOD_ADDR = 0x89
    TL0_ADDR = 0x8A
    TL1_ADDR = 0x8B
    TH0_ADDR = 0x8C
    TH1_ADDR = 0x8D
    IE_ADDR = 0xA8
    IP_ADDR = 0xB8
    PSW_ADDR = 0xD0
    ACC_ADDR = 0xE0
    B_ADDR = 0xF0
    SCON_ADDR = 0x98
    SBUF_ADDR = 0x99

    CY_MASK = 0x80
    AC_MASK = 0x40
    RS1_MASK = 0x10
    RS0_MASK = 0x08
    OV_MASK = 0x04
    P_MASK = 0x01

    TF1_MASK = 0x80
    TR1_MASK = 0x40
    TF0_MASK = 0x20
    TR0_MASK = 0x10
    IE1_MASK = 0x08
    IT1_MASK = 0x04
    IE0_MASK = 0x02
    IT0_MASK = 0x01

    RI_MASK = 0x01
    TI_MASK = 0x02
    REN_MASK = 0x10

    EA_MASK = 0x80
    ES_MASK = 0x10
    ET1_MASK = 0x08
    EX1_MASK = 0x04
    ET0_MASK = 0x02
    EX0_MASK = 0x01

    PORT_NAMES = {
        0x80: "P0",
        0x90: "P1",
        0xA0: "P2",
        0xB0: "P3",
    }

    TIMER_ADDRS = (
        (TL0_ADDR, TH0_ADDR, TR0_MASK, TF0_MASK),
        (TL1_ADDR, TH1_ADDR, TR1_MASK, TF1_MASK),
    )

    INTERRUPT_LAYOUT = (
        ("EX0", 0x0003, EX0_MASK, TCON_ADDR, IE0_MASK),
        ("T0", 0x000B, ET0_MASK, TCON_ADDR, TF0_MASK),
        ("EX1", 0x0013, EX1_MASK, TCON_ADDR, IE1_MASK),
        ("T1", 0x001B, ET1_MASK, TCON_ADDR, TF1_MASK),
        ("SER", 0x0023, ES_MASK, None, None),
    )

    def __init__(
        self,
        code_memory: bytes | bytearray,
        entry_point: int = 0,
        serial_input: bytes | bytearray | str | None = None,
    ) -> None:
        if len(code_memory) > 0x10000:
            raise ValueError("8051 code memory is limited to 64 KiB")

        image = bytearray(0x10000)
        image[: len(code_memory)] = code_memory[:0x10000]
        self.code_memory = bytes(image)

        self.iram = bytearray(256)
        self.xram = bytearray(0x10000)
        self.sfr = bytearray(128)
        self.serial_output = bytearray()
        self.serial_input_queue: deque[int] = deque()
        self.serial_rx_buffer = 0
        self.port_log: list[tuple[int, str, int]] = []
        self.interrupt_log: list[tuple[int, str, int]] = []
        self.interrupt_stack: list[int] = []
        self.external_interrupt_lines = [1, 1]
        self.external_interrupt_pulses = [False, False]
        self._ext_counter_pulses = [0, 0]
        self.instructions = 0
        self.ticks = 0
        self.pc = 0
        self.reset(entry_point=entry_point)

        if serial_input:
            self.queue_serial_input(serial_input)

    def reset(self, entry_point: int = 0) -> None:
        self.iram[:] = b"\x00" * len(self.iram)
        self.xram[:] = b"\x00" * len(self.xram)
        self.sfr[:] = b"\x00" * len(self.sfr)
        self.serial_output.clear()
        self.serial_input_queue.clear()
        self.serial_rx_buffer = 0
        self.port_log.clear()
        self.interrupt_log.clear()
        self.interrupt_stack.clear()
        self.external_interrupt_lines[:] = [1, 1]
        self.external_interrupt_pulses[:] = [False, False]
        self._ext_counter_pulses[:] = [0, 0]
        self.instructions = 0
        self.ticks = 0
        self.pc = entry_point & 0xFFFF

        self._write_sfr_raw(self.SP_ADDR, 0x07)
        self._write_sfr_raw(0x80, 0xFF)
        self._write_sfr_raw(0x90, 0xFF)
        self._write_sfr_raw(0xA0, 0xFF)
        self._write_sfr_raw(0xB0, 0xFF)
        self._update_parity()

    def queue_serial_input(self, data: bytes | bytearray | str) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        self.serial_input_queue.extend(payload)

    def request_external_interrupt(self, index: int) -> None:
        if index == 0:
            if self._get_sfr(self.TCON_ADDR) & self.IT0_MASK:
                self._write_sfr_raw(self.TCON_ADDR, self._get_sfr(self.TCON_ADDR) | self.IE0_MASK)
            else:
                self.external_interrupt_pulses[0] = True
                self._update_external_interrupt_flags()
            return
        if index == 1:
            if self._get_sfr(self.TCON_ADDR) & self.IT1_MASK:
                self._write_sfr_raw(self.TCON_ADDR, self._get_sfr(self.TCON_ADDR) | self.IE1_MASK)
            else:
                self.external_interrupt_pulses[1] = True
                self._update_external_interrupt_flags()
            return
        raise ValueError("External interrupt index must be 0 or 1")

    def pulse_external_counter(self, index: int, count: int = 1) -> None:
        if index not in (0, 1):
            raise ValueError("External counter index must be 0 or 1")
        self._ext_counter_pulses[index] += count

    def set_external_interrupt_line(self, index: int, state: bool | int) -> None:
        if index not in (0, 1):
            raise ValueError("External interrupt index must be 0 or 1")

        level = 1 if state else 0
        previous_level = self.external_interrupt_lines[index]
        self.external_interrupt_lines[index] = level

        tcon = self._get_sfr(self.TCON_ADDR)
        flag_mask = self.IE0_MASK if index == 0 else self.IE1_MASK
        trigger_mask = self.IT0_MASK if index == 0 else self.IT1_MASK
        edge_triggered = bool(tcon & trigger_mask)

        if edge_triggered:
            if previous_level and not level:
                self._write_sfr_raw(self.TCON_ADDR, tcon | flag_mask)
            return

        self._update_external_interrupt_flags()

    def _update_external_interrupt_flags(self) -> None:
        tcon = self._get_sfr(self.TCON_ADDR)
        for index, (flag_mask, trigger_mask) in enumerate(
            (
                (self.IE0_MASK, self.IT0_MASK),
                (self.IE1_MASK, self.IT1_MASK),
            )
        ):
            if tcon & trigger_mask:
                continue

            active_low = self.external_interrupt_lines[index] == 0
            if active_low or self.external_interrupt_pulses[index]:
                tcon |= flag_mask
            else:
                tcon &= ~flag_mask

        self._write_sfr_raw(self.TCON_ADDR, tcon)

    def _timer0_split_mode_enabled(self) -> bool:
        return self._timer_mode(0) == 0x03

    def _timer0_split_high_should_tick(self) -> bool:
        return self._timer0_split_mode_enabled() and bool(self._get_sfr(self.TCON_ADDR) & self.TR1_MASK)

    def _tick_timer0_split_high(self) -> None:
        if not self._timer0_split_high_should_tick():
            return

        value = self._get_sfr(self.TH0_ADDR) + 1
        if value > 0xFF:
            self._write_sfr_raw(self.TH0_ADDR, 0x00)
            self._set_tcon_flag(self.TF1_MASK, True)
            return
        self._write_sfr_raw(self.TH0_ADDR, value & 0xFF)

    @property
    def acc(self) -> int:
        return self._get_sfr(self.ACC_ADDR)

    @acc.setter
    def acc(self, value: int) -> None:
        self._set_sfr(self.ACC_ADDR, value)

    @property
    def b(self) -> int:
        return self._get_sfr(self.B_ADDR)

    @b.setter
    def b(self, value: int) -> None:
        self._set_sfr(self.B_ADDR, value)

    @property
    def psw(self) -> int:
        return self._get_sfr(self.PSW_ADDR)

    @psw.setter
    def psw(self, value: int) -> None:
        self._set_sfr(self.PSW_ADDR, value)

    @property
    def sp(self) -> int:
        return self._get_sfr(self.SP_ADDR)

    @sp.setter
    def sp(self, value: int) -> None:
        self._set_sfr(self.SP_ADDR, value)

    @property
    def dptr(self) -> int:
        return (self._get_sfr(self.DPH_ADDR) << 8) | self._get_sfr(self.DPL_ADDR)

    @dptr.setter
    def dptr(self, value: int) -> None:
        masked = value & 0xFFFF
        self._set_sfr(self.DPH_ADDR, masked >> 8)
        self._set_sfr(self.DPL_ADDR, masked & 0xFF)

    def _get_sfr(self, address: int) -> int:
        return self.sfr[(address & 0xFF) - 0x80]

    def _write_sfr_raw(self, address: int, value: int) -> None:
        self.sfr[(address & 0xFF) - 0x80] = value & 0xFF

    def _set_sfr(self, address: int, value: int) -> None:
        address &= 0xFF
        masked = value & 0xFF
        old_value = self._get_sfr(address)
        self._write_sfr_raw(address, masked)

        if address in self.PORT_NAMES and old_value != masked:
            self.port_log.append((self.instructions, self.PORT_NAMES[address], masked))

        if address == self.SBUF_ADDR:
            self.serial_output.append(masked)
            self._write_sfr_raw(self.SCON_ADDR, self._get_sfr(self.SCON_ADDR) | self.TI_MASK)
        elif address == self.TCON_ADDR:
            self._update_external_interrupt_flags()

    def read_direct(self, address: int) -> int:
        masked = address & 0xFF
        if masked < 0x80:
            return self.iram[masked]
        if masked == self.SBUF_ADDR:
            return self.serial_rx_buffer
        return self._get_sfr(masked)

    def write_direct(self, address: int, value: int) -> None:
        masked = address & 0xFF
        if masked < 0x80:
            self.iram[masked] = value & 0xFF
            return
        self._set_sfr(masked, value)

    def _indirect_address(self, index: int) -> int:
        return self.get_reg(index) & 0xFF

    def read_indirect(self, index: int) -> int:
        return self.iram[self._indirect_address(index)]

    def write_indirect(self, index: int, value: int) -> None:
        self.iram[self._indirect_address(index)] = value & 0xFF

    def _fetch_byte(self) -> int:
        value = self.code_memory[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        return value

    def _fetch_word(self) -> int:
        high = self._fetch_byte()
        low = self._fetch_byte()
        return (high << 8) | low

    def _read_code(self, address: int) -> int:
        return self.code_memory[address & 0xFFFF]

    def _signed_offset(self, value: int) -> int:
        return value - 0x100 if value & 0x80 else value

    def _branch_relative(self, offset: int) -> None:
        self.pc = (self.pc + self._signed_offset(offset)) & 0xFFFF

    def _ajmp_target(self, opcode: int, low_byte: int) -> int:
        return (self.pc & 0xF800) | ((opcode & 0xE0) << 3) | low_byte

    def _get_flag(self, mask: int) -> int:
        return 1 if self.psw & mask else 0

    def _set_flag(self, mask: int, state: bool | int) -> None:
        value = self.psw
        if state:
            value |= mask
        else:
            value &= ~mask
        self.psw = value

    def _update_parity(self) -> None:
        parity = self.acc.bit_count() & 1
        value = self.psw & ~self.P_MASK
        self.psw = value | parity

    def get_reg(self, index: int) -> int:
        bank = ((self.psw & (self.RS1_MASK | self.RS0_MASK)) >> 3) * 8
        return self.iram[bank + (index & 0x07)]

    def set_reg(self, index: int, value: int) -> None:
        bank = ((self.psw & (self.RS1_MASK | self.RS0_MASK)) >> 3) * 8
        self.iram[bank + (index & 0x07)] = value & 0xFF

    def _read_bit(self, bit_address: int) -> int:
        bit_address &= 0xFF
        if bit_address < 0x80:
            byte_address = 0x20 + (bit_address >> 3)
        else:
            byte_address = bit_address & 0xF8
        bit = bit_address & 0x07
        return 1 if self.read_direct(byte_address) & (1 << bit) else 0

    def _write_bit(self, bit_address: int, state: bool | int) -> None:
        bit_address &= 0xFF
        if bit_address < 0x80:
            byte_address = 0x20 + (bit_address >> 3)
        else:
            byte_address = bit_address & 0xF8
        bit = bit_address & 0x07
        value = self.read_direct(byte_address)
        if state:
            value |= 1 << bit
        else:
            value &= ~(1 << bit)
        self.write_direct(byte_address, value)

    def _push_byte(self, value: int) -> None:
        self.sp = (self.sp + 1) & 0xFF
        self.iram[self.sp] = value & 0xFF

    def _pop_byte(self) -> int:
        value = self.iram[self.sp]
        self.sp = (self.sp - 1) & 0xFF
        return value

    def _push_return_address(self, address: int) -> None:
        self._push_byte(address & 0xFF)
        self._push_byte((address >> 8) & 0xFF)

    def _pop_return_address(self) -> int:
        high = self._pop_byte()
        low = self._pop_byte()
        return (high << 8) | low

    def _read_acc_source(self, low_nibble: int) -> int:
        if low_nibble == 0x4:
            return self._fetch_byte()
        if low_nibble == 0x5:
            return self.read_direct(self._fetch_byte())
        if low_nibble in (0x6, 0x7):
            return self.read_indirect(low_nibble & 0x01)
        if 0x8 <= low_nibble <= 0xF:
            return self.get_reg(low_nibble & 0x07)
        raise UnsupportedOpcodeError(f"Unsupported source mode 0x{low_nibble:X}")

    def _add_to_acc(self, value: int, carry_in: int = 0) -> None:
        a = self.acc
        result = a + (value & 0xFF) + carry_in
        masked = result & 0xFF
        self._set_flag(self.CY_MASK, result > 0xFF)
        self._set_flag(self.AC_MASK, ((a & 0x0F) + (value & 0x0F) + carry_in) > 0x0F)
        self._set_flag(
            self.OV_MASK,
            (~(a ^ value) & (a ^ masked) & 0x80) != 0,
        )
        self.acc = masked

    def _subb_from_acc(self, value: int) -> None:
        a = self.acc
        borrow = self._get_flag(self.CY_MASK)
        result = a - (value & 0xFF) - borrow
        masked = result & 0xFF
        self._set_flag(self.CY_MASK, result < 0)
        self._set_flag(self.AC_MASK, ((a & 0x0F) - (value & 0x0F) - borrow) < 0)
        self._set_flag(
            self.OV_MASK,
            ((a ^ value) & (a ^ masked) & 0x80) != 0,
        )
        self.acc = masked

    def _compare_and_jump(self, lhs: int, rhs: int, offset: int) -> None:
        self._set_flag(self.CY_MASK, (lhs & 0xFF) < (rhs & 0xFF))
        if (lhs & 0xFF) != (rhs & 0xFF):
            self._branch_relative(offset)

    def _serial_can_deliver(self) -> bool:
        return (
            bool(self.serial_input_queue)
            and bool(self._get_sfr(self.SCON_ADDR) & self.REN_MASK)
            and not bool(self._get_sfr(self.SCON_ADDR) & self.RI_MASK)
        )

    def _deliver_serial_input(self) -> None:
        if not self._serial_can_deliver():
            return

        self.serial_rx_buffer = self.serial_input_queue.popleft()
        self._write_sfr_raw(self.SCON_ADDR, self._get_sfr(self.SCON_ADDR) | self.RI_MASK)

    def _timer_nibble(self, index: int) -> int:
        tmod = self._get_sfr(self.TMOD_ADDR)
        return (tmod >> (4 * index)) & 0x0F

    def _timer_mode(self, index: int) -> int:
        return self._timer_nibble(index) & 0x03

    def _timer_counter_mode(self, index: int) -> bool:
        return bool(self._timer_nibble(index) & 0x04)

    def _timer_should_tick(self, index: int) -> bool:
        _, _, run_mask, _ = self.TIMER_ADDRS[index]
        if self._timer_counter_mode(index):
            if self._ext_counter_pulses[index] > 0:
                self._ext_counter_pulses[index] -= 1
                return bool(self._get_sfr(self.TCON_ADDR) & run_mask)
            return False
        if index == 1 and self._timer0_split_mode_enabled():
            return False
        if self._timer_mode(index) == 0x03 and index != 0:
            return False
        return bool(self._get_sfr(self.TCON_ADDR) & run_mask)

    def _set_tcon_flag(self, mask: int, state: bool) -> None:
        value = self._get_sfr(self.TCON_ADDR)
        if state:
            value |= mask
        else:
            value &= ~mask
        self._write_sfr_raw(self.TCON_ADDR, value)

    def _tick_timer(self, index: int) -> None:
        if not self._timer_should_tick(index):
            return

        tl_addr, th_addr, _, tf_mask = self.TIMER_ADDRS[index]
        mode = self._timer_mode(index)
        tl = self._get_sfr(tl_addr)
        th = self._get_sfr(th_addr)

        if index == 0 and mode == 0x03:
            value = tl + 1
            if value > 0xFF:
                self._write_sfr_raw(tl_addr, 0x00)
                self._set_tcon_flag(tf_mask, True)
            else:
                self._write_sfr_raw(tl_addr, value & 0xFF)
            return

        if mode == 0x00:
            value = (th << 5) | (tl & 0x1F)
            value += 1
            overflow = value > 0x1FFF
            value &= 0x1FFF
            self._write_sfr_raw(th_addr, (value >> 5) & 0xFF)
            self._write_sfr_raw(tl_addr, (tl & 0xE0) | (value & 0x1F))
            if overflow:
                self._set_tcon_flag(tf_mask, True)
            return

        if mode == 0x01:
            value = ((th << 8) | tl) + 1
            overflow = value > 0xFFFF
            value &= 0xFFFF
            self._write_sfr_raw(th_addr, (value >> 8) & 0xFF)
            self._write_sfr_raw(tl_addr, value & 0xFF)
            if overflow:
                self._set_tcon_flag(tf_mask, True)
            return

        if mode == 0x02:
            value = tl + 1
            if value > 0xFF:
                self._write_sfr_raw(tl_addr, th)
                self._set_tcon_flag(tf_mask, True)
            else:
                self._write_sfr_raw(tl_addr, value & 0xFF)

    def _tick_peripherals(self, ticks: int = 1) -> None:
        for _ in range(ticks):
            self._update_external_interrupt_flags()
            self._tick_timer(0)
            if self._timer0_split_mode_enabled():
                self._tick_timer0_split_high()
            else:
                self._tick_timer(1)
            self._deliver_serial_input()

    def _serial_interrupt_pending(self) -> bool:
        return bool(self._get_sfr(self.SCON_ADDR) & (self.RI_MASK | self.TI_MASK))

    def _current_interrupt_priority(self) -> int:
        if not self.interrupt_stack:
            return -1
        return self.interrupt_stack[-1]

    def _select_interrupt(self) -> tuple[str, int, int, int | None, int | None] | None:
        self._update_external_interrupt_flags()
        ie = self._get_sfr(self.IE_ADDR)
        if not (ie & self.EA_MASK):
            return None

        ip = self._get_sfr(self.IP_ADDR)
        current_priority = self._current_interrupt_priority()

        for desired_priority in (1, 0):
            if desired_priority <= current_priority:
                continue

            for name, vector, mask, clear_addr, clear_mask in self.INTERRUPT_LAYOUT:
                if not (ie & mask):
                    continue

                if name == "SER":
                    pending = self._serial_interrupt_pending()
                elif clear_addr is not None and clear_mask is not None:
                    pending = bool(self._get_sfr(clear_addr) & clear_mask)
                else:
                    pending = False

                if not pending:
                    continue

                priority = 1 if (ip & mask) else 0
                if priority == desired_priority:
                    return name, vector, priority, clear_addr, clear_mask

        return None

    def _interrupts_can_progress(self) -> bool:
        return self._select_interrupt() is not None

    def _background_activity_expected(self) -> bool:
        return (
            self._timer_should_tick(0)
            or self._timer_should_tick(1)
            or self._timer0_split_high_should_tick()
            or self._serial_can_deliver()
            or self._interrupts_can_progress()
        )

    def _service_interrupt(self) -> bool:
        selection = self._select_interrupt()
        if selection is None:
            return False

        name, vector, priority, clear_addr, clear_mask = selection
        if name == "EX0":
            self.external_interrupt_pulses[0] = False
        elif name == "EX1":
            self.external_interrupt_pulses[1] = False
        if clear_addr is not None and clear_mask is not None:
            self._write_sfr_raw(clear_addr, self._get_sfr(clear_addr) & ~clear_mask)

        self._push_return_address(self.pc)
        self.interrupt_stack.append(priority)
        self.interrupt_log.append((self.instructions, name, vector))
        self.pc = vector
        return True

    def _return_from_interrupt(self) -> None:
        self.pc = self._pop_return_address()
        if self.interrupt_stack:
            self.interrupt_stack.pop()

    def step(self) -> int:
        opcode = self._fetch_byte()

        if opcode == 0x00:
            pass
        elif opcode & 0x1F == 0x01:
            low = self._fetch_byte()
            self.pc = self._ajmp_target(opcode, low)
        elif opcode == 0x02:
            self.pc = self._fetch_word()
        elif opcode == 0x03:
            self.acc = ((self.acc >> 1) | ((self.acc & 0x01) << 7)) & 0xFF
        elif 0x04 <= opcode <= 0x0F:
            low = opcode & 0x0F
            if low == 0x04:
                self.acc = (self.acc + 1) & 0xFF
            elif low == 0x05:
                address = self._fetch_byte()
                self.write_direct(address, (self.read_direct(address) + 1) & 0xFF)
            elif low in (0x06, 0x07):
                value = (self.read_indirect(low & 0x01) + 1) & 0xFF
                self.write_indirect(low & 0x01, value)
            else:
                register = low & 0x07
                self.set_reg(register, (self.get_reg(register) + 1) & 0xFF)
        elif opcode == 0x10:
            bit_address = self._fetch_byte()
            offset = self._fetch_byte()
            if self._read_bit(bit_address):
                self._write_bit(bit_address, 0)
                self._branch_relative(offset)
        elif opcode & 0x1F == 0x11:
            low = self._fetch_byte()
            target = self._ajmp_target(opcode, low)
            self._push_return_address(self.pc)
            self.pc = target
        elif opcode == 0x12:
            target = self._fetch_word()
            self._push_return_address(self.pc)
            self.pc = target
        elif opcode == 0x13:
            carry = self._get_flag(self.CY_MASK)
            new_carry = self.acc & 0x01
            self.acc = ((carry << 7) | (self.acc >> 1)) & 0xFF
            self._set_flag(self.CY_MASK, new_carry)
        elif 0x14 <= opcode <= 0x1F:
            low = opcode & 0x0F
            if low == 0x04:
                self.acc = (self.acc - 1) & 0xFF
            elif low == 0x05:
                address = self._fetch_byte()
                self.write_direct(address, (self.read_direct(address) - 1) & 0xFF)
            elif low in (0x06, 0x07):
                value = (self.read_indirect(low & 0x01) - 1) & 0xFF
                self.write_indirect(low & 0x01, value)
            else:
                register = low & 0x07
                self.set_reg(register, (self.get_reg(register) - 1) & 0xFF)
        elif opcode == 0x20:
            bit_address = self._fetch_byte()
            offset = self._fetch_byte()
            if self._read_bit(bit_address):
                self._branch_relative(offset)
        elif opcode == 0x22:
            self.pc = self._pop_return_address()
        elif opcode == 0x23:
            self.acc = ((self.acc << 1) | (self.acc >> 7)) & 0xFF
        elif 0x24 <= opcode <= 0x2F:
            self._add_to_acc(self._read_acc_source(opcode & 0x0F), carry_in=0)
        elif opcode == 0x30:
            bit_address = self._fetch_byte()
            offset = self._fetch_byte()
            if not self._read_bit(bit_address):
                self._branch_relative(offset)
        elif opcode == 0x32:
            self._return_from_interrupt()
        elif opcode == 0x33:
            carry = self._get_flag(self.CY_MASK)
            new_carry = 1 if self.acc & 0x80 else 0
            self.acc = ((self.acc << 1) | carry) & 0xFF
            self._set_flag(self.CY_MASK, new_carry)
        elif 0x34 <= opcode <= 0x3F:
            self._add_to_acc(
                self._read_acc_source(opcode & 0x0F),
                carry_in=self._get_flag(self.CY_MASK),
            )
        elif opcode == 0x40:
            offset = self._fetch_byte()
            if self._get_flag(self.CY_MASK):
                self._branch_relative(offset)
        elif opcode == 0x42:
            address = self._fetch_byte()
            self.write_direct(address, self.read_direct(address) | self.acc)
        elif opcode == 0x43:
            address = self._fetch_byte()
            immediate = self._fetch_byte()
            self.write_direct(address, self.read_direct(address) | immediate)
        elif 0x44 <= opcode <= 0x4F:
            self.acc = self.acc | self._read_acc_source(opcode & 0x0F)
        elif opcode == 0x50:
            offset = self._fetch_byte()
            if not self._get_flag(self.CY_MASK):
                self._branch_relative(offset)
        elif opcode == 0x52:
            address = self._fetch_byte()
            self.write_direct(address, self.read_direct(address) & self.acc)
        elif opcode == 0x53:
            address = self._fetch_byte()
            immediate = self._fetch_byte()
            self.write_direct(address, self.read_direct(address) & immediate)
        elif 0x54 <= opcode <= 0x5F:
            self.acc = self.acc & self._read_acc_source(opcode & 0x0F)
        elif opcode == 0x60:
            offset = self._fetch_byte()
            if self.acc == 0:
                self._branch_relative(offset)
        elif opcode == 0x62:
            address = self._fetch_byte()
            self.write_direct(address, self.read_direct(address) ^ self.acc)
        elif opcode == 0x63:
            address = self._fetch_byte()
            immediate = self._fetch_byte()
            self.write_direct(address, self.read_direct(address) ^ immediate)
        elif 0x64 <= opcode <= 0x6F:
            self.acc = self.acc ^ self._read_acc_source(opcode & 0x0F)
        elif opcode == 0x70:
            offset = self._fetch_byte()
            if self.acc != 0:
                self._branch_relative(offset)
        elif opcode == 0x72:
            bit_address = self._fetch_byte()
            self._set_flag(self.CY_MASK, self._get_flag(self.CY_MASK) or self._read_bit(bit_address))
        elif opcode == 0x73:
            self.pc = (self.dptr + self.acc) & 0xFFFF
        elif opcode == 0x74:
            self.acc = self._fetch_byte()
        elif opcode == 0x75:
            address = self._fetch_byte()
            immediate = self._fetch_byte()
            self.write_direct(address, immediate)
        elif opcode in (0x76, 0x77):
            self.write_indirect(opcode & 0x01, self._fetch_byte())
        elif 0x78 <= opcode <= 0x7F:
            self.set_reg(opcode & 0x07, self._fetch_byte())
        elif opcode == 0x80:
            self._branch_relative(self._fetch_byte())
        elif opcode == 0x82:
            bit_address = self._fetch_byte()
            self._set_flag(self.CY_MASK, self._get_flag(self.CY_MASK) and self._read_bit(bit_address))
        elif opcode == 0x83:
            self.acc = self._read_code((self.pc + self.acc) & 0xFFFF)
        elif opcode == 0x84:
            divisor = self.b
            if divisor == 0:
                self._set_flag(self.OV_MASK, 1)
                self._set_flag(self.CY_MASK, 0)
            else:
                quotient, remainder = divmod(self.acc, divisor)
                self.acc = quotient & 0xFF
                self.b = remainder & 0xFF
                self._set_flag(self.OV_MASK, 0)
                self._set_flag(self.CY_MASK, 0)
        elif opcode == 0x85:
            source = self._fetch_byte()
            destination = self._fetch_byte()
            self.write_direct(destination, self.read_direct(source))
        elif opcode in (0x86, 0x87):
            destination = self._fetch_byte()
            self.write_direct(destination, self.read_indirect(opcode & 0x01))
        elif 0x88 <= opcode <= 0x8F:
            destination = self._fetch_byte()
            self.write_direct(destination, self.get_reg(opcode & 0x07))
        elif opcode == 0x90:
            self.dptr = self._fetch_word()
        elif opcode == 0x92:
            bit_address = self._fetch_byte()
            self._write_bit(bit_address, self._get_flag(self.CY_MASK))
        elif opcode == 0x93:
            self.acc = self._read_code((self.dptr + self.acc) & 0xFFFF)
        elif 0x94 <= opcode <= 0x9F:
            self._subb_from_acc(self._read_acc_source(opcode & 0x0F))
        elif opcode == 0xA0:
            bit_address = self._fetch_byte()
            self._set_flag(
                self.CY_MASK,
                self._get_flag(self.CY_MASK) or (1 - self._read_bit(bit_address)),
            )
        elif opcode == 0xA2:
            bit_address = self._fetch_byte()
            self._set_flag(self.CY_MASK, self._read_bit(bit_address))
        elif opcode == 0xA3:
            self.dptr = (self.dptr + 1) & 0xFFFF
        elif opcode == 0xA4:
            product = self.acc * self.b
            self.acc = product & 0xFF
            self.b = (product >> 8) & 0xFF
            self._set_flag(self.CY_MASK, 0)
            self._set_flag(self.OV_MASK, self.b != 0)
        elif opcode == 0xA5:
            raise UnsupportedOpcodeError("Opcode 0xA5 is undefined on classic 8051")
        elif opcode in (0xA6, 0xA7):
            source = self._fetch_byte()
            self.write_indirect(opcode & 0x01, self.read_direct(source))
        elif 0xA8 <= opcode <= 0xAF:
            source = self._fetch_byte()
            self.set_reg(opcode & 0x07, self.read_direct(source))
        elif opcode == 0xB0:
            bit_address = self._fetch_byte()
            self._set_flag(
                self.CY_MASK,
                self._get_flag(self.CY_MASK) and (1 - self._read_bit(bit_address)),
            )
        elif opcode == 0xB2:
            bit_address = self._fetch_byte()
            self._write_bit(bit_address, not self._read_bit(bit_address))
        elif opcode == 0xB3:
            self._set_flag(self.CY_MASK, not self._get_flag(self.CY_MASK))
        elif opcode == 0xB4:
            immediate = self._fetch_byte()
            offset = self._fetch_byte()
            self._compare_and_jump(self.acc, immediate, offset)
        elif opcode == 0xB5:
            address = self._fetch_byte()
            offset = self._fetch_byte()
            self._compare_and_jump(self.acc, self.read_direct(address), offset)
        elif opcode in (0xB6, 0xB7):
            immediate = self._fetch_byte()
            offset = self._fetch_byte()
            self._compare_and_jump(self.read_indirect(opcode & 0x01), immediate, offset)
        elif 0xB8 <= opcode <= 0xBF:
            immediate = self._fetch_byte()
            offset = self._fetch_byte()
            self._compare_and_jump(self.get_reg(opcode & 0x07), immediate, offset)
        elif opcode == 0xC0:
            address = self._fetch_byte()
            if address == self.SP_ADDR:
                self.sp = (self.sp + 1) & 0xFF
                self.iram[self.sp] = self.sp
            else:
                self._push_byte(self.read_direct(address))
        elif opcode == 0xC2:
            self._write_bit(self._fetch_byte(), 0)
        elif opcode == 0xC3:
            self._set_flag(self.CY_MASK, 0)
        elif opcode == 0xC4:
            self.acc = ((self.acc & 0x0F) << 4) | ((self.acc & 0xF0) >> 4)
        elif opcode == 0xC5:
            address = self._fetch_byte()
            value = self.read_direct(address)
            self.write_direct(address, self.acc)
            self.acc = value
        elif opcode in (0xC6, 0xC7):
            index = opcode & 0x01
            value = self.read_indirect(index)
            self.write_indirect(index, self.acc)
            self.acc = value
        elif 0xC8 <= opcode <= 0xCF:
            index = opcode & 0x07
            value = self.get_reg(index)
            self.set_reg(index, self.acc)
            self.acc = value
        elif opcode == 0xD0:
            address = self._fetch_byte()
            value = self.iram[self.sp]
            if address == self.SP_ADDR:
                self.sp = (value - 1) & 0xFF
            else:
                self.write_direct(address, value)
                self.sp = (self.sp - 1) & 0xFF
        elif opcode == 0xD2:
            self._write_bit(self._fetch_byte(), 1)
        elif opcode == 0xD3:
            self._set_flag(self.CY_MASK, 1)
        elif opcode == 0xD4:
            a = self.acc
            correction = 0
            carry = self._get_flag(self.CY_MASK)
            if self._get_flag(self.AC_MASK) or (a & 0x0F) > 9:
                correction |= 0x06
            if carry or a > 0x99:
                correction |= 0x60
            result = a + correction
            self.acc = result & 0xFF
            self._set_flag(self.CY_MASK, carry or result > 0xFF)
        elif opcode == 0xD5:
            address = self._fetch_byte()
            offset = self._fetch_byte()
            value = (self.read_direct(address) - 1) & 0xFF
            self.write_direct(address, value)
            if value != 0:
                self._branch_relative(offset)
        elif opcode in (0xD6, 0xD7):
            index = opcode & 0x01
            value = self.read_indirect(index)
            new_acc = (self.acc & 0xF0) | (value & 0x0F)
            new_value = (value & 0xF0) | (self.acc & 0x0F)
            self.acc = new_acc
            self.write_indirect(index, new_value)
        elif 0xD8 <= opcode <= 0xDF:
            index = opcode & 0x07
            value = (self.get_reg(index) - 1) & 0xFF
            self.set_reg(index, value)
            offset = self._fetch_byte()
            if value != 0:
                self._branch_relative(offset)
        elif opcode == 0xE0:
            self.acc = self.xram[self.dptr]
        elif opcode in (0xE2, 0xE3):
            address = ((self.read_direct(0xA0) << 8) | self.get_reg(opcode & 0x01)) & 0xFFFF
            self.acc = self.xram[address]
        elif opcode == 0xE4:
            self.acc = 0
        elif opcode == 0xE5:
            self.acc = self.read_direct(self._fetch_byte())
        elif opcode in (0xE6, 0xE7):
            self.acc = self.read_indirect(opcode & 0x01)
        elif 0xE8 <= opcode <= 0xEF:
            self.acc = self.get_reg(opcode & 0x07)
        elif opcode == 0xF0:
            self.xram[self.dptr] = self.acc
        elif opcode in (0xF2, 0xF3):
            address = ((self.read_direct(0xA0) << 8) | self.get_reg(opcode & 0x01)) & 0xFFFF
            self.xram[address] = self.acc
        elif opcode == 0xF4:
            self.acc = (~self.acc) & 0xFF
        elif opcode == 0xF5:
            self.write_direct(self._fetch_byte(), self.acc)
        elif opcode in (0xF6, 0xF7):
            self.write_indirect(opcode & 0x01, self.acc)
        elif 0xF8 <= opcode <= 0xFF:
            self.set_reg(opcode & 0x07, self.acc)
        else:
            raise UnsupportedOpcodeError(
                f"Unsupported opcode 0x{opcode:02X} at PC 0x{(self.pc - 1) & 0xFFFF:04X}"
            )

        self._update_parity()
        return opcode

    def run(
        self,
        max_instructions: int = 100_000,
        detect_tight_loops: bool = True,
        tight_loop_threshold: int = 4,
        before_step: Callable[["MCS51", int], str | None] | None = None,
        after_step: Callable[["MCS51", int, int, int], str | None] | None = None,
    ) -> ExecutionResult:
        if max_instructions <= 0:
            raise ValueError("max_instructions must be positive")

        halt_reason = "max_instructions"
        repeated_pc = 0

        for _ in range(max_instructions):
            if before_step is not None:
                halt = before_step(self, self.ticks)
                if halt:
                    halt_reason = halt
                    break
            previous_pc = self.pc
            opcode = self.step()
            self.instructions += 1
            self.ticks += 1
            if after_step is not None:
                halt = after_step(self, previous_pc, opcode, self.instructions)
                if halt:
                    halt_reason = halt
                    break
            self._tick_peripherals()
            self._service_interrupt()

            if detect_tight_loops and self.pc == previous_pc:
                if self._background_activity_expected():
                    repeated_pc = 0
                else:
                    repeated_pc += 1
                    if repeated_pc >= tight_loop_threshold:
                        halt_reason = "tight_loop"
                        break
            else:
                repeated_pc = 0
        else:
            halt_reason = "max_instructions"

        return ExecutionResult(
            instructions=self.instructions,
            ticks=self.ticks,
            halt_reason=halt_reason,
            serial_output=bytes(self.serial_output),
            port_log=tuple(self.port_log),
            interrupt_log=tuple(self.interrupt_log),
        )
