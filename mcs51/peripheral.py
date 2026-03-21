"""I2C / SPI 总线模拟器（bit-bang 时序检测）。"""
from __future__ import annotations

from collections import deque

# ---------------------------------------------------------------------------
# 固定引脚映射
# I2C: SCL=P3.6(SFR 0xB0 bit6), SDA=P3.7(SFR 0xB0 bit7)
# SPI: SCK=P1.4, MOSI=P1.5, MISO=P1.6, CS=P1.7  (SFR 0x90)
# ---------------------------------------------------------------------------

_I2C_PORT = 0xB0
_SCL_BIT = 6  # bit mask 0x40
_SDA_BIT = 7  # bit mask 0x80

_SPI_PORT = 0x90
_SCK_BIT = 4   # 0x10
_MOSI_BIT = 5  # 0x20
_MISO_BIT = 6  # 0x40
_CS_BIT = 7    # 0x80


# ---------------------------------------------------------------------------
# I2CBus
# ---------------------------------------------------------------------------

class I2CBus:
    """模拟 I2C 主机端时序解析（SCL=P3.6, SDA=P3.7）。"""

    # 状态常量
    _IDLE = 0
    _START = 1   # 已检测到 START，等第 1 位
    _ADDR = 2    # 接收地址+R/W（8 bits）
    _ACK1 = 3   # 地址 ACK 阶段
    _DATA = 4    # 接收/发送数据字节
    _ACK2 = 5   # 数据 ACK 阶段

    def __init__(self) -> None:
        self.log: list[tuple[int, str]] = []
        self._tx_queue: deque[int] = deque()
        self.reset()

    def reset(self) -> None:
        self._state = self._IDLE
        self._shift = 0       # 移位寄存器（接收中的字节，MSB first）
        self._bit_count = 0   # 已收到多少位
        self._addr_byte = 0   # 地址字节（含 R/W 位）
        self._is_read = False
        self._tx_bit_index = 0  # 当前发送字节的位索引（7..0）
        self._tx_current = 0xFF  # 当前发送字节
        self._byte_index = 0   # 事务内第几个数据字节
        # SDA/SCL 上一时刻的值（用于边沿检测）
        self._prev_scl = 1
        self._prev_sda = 1
        self.log.clear()
        self._tx_queue.clear()

    @property
    def has_pending(self) -> bool:
        return len(self._tx_queue) > 0

    def inject_response(self, data: bytes) -> None:
        """向 tx_queue 压入要回复给 MCU 的字节序列。"""
        for b in data:
            self._tx_queue.append(b)

    def on_port_write(self, port: int, old_val: int, new_val: int, tick: int) -> None:
        """CPU 写端口时调用；只关心 P3（0xB0）。"""
        if port != _I2C_PORT:
            return

        scl = (new_val >> _SCL_BIT) & 1
        sda = (new_val >> _SDA_BIT) & 1
        prev_scl = self._prev_scl
        prev_sda = self._prev_sda
        self._prev_scl = scl
        self._prev_sda = sda

        # START 条件：SCL=1 时 SDA 下降沿
        if scl == 1 and prev_scl == 1 and sda == 0 and prev_sda == 1:
            self._state = _I2CBus_start(self)
            self.log.append((tick, "START"))
            return

        # STOP 条件：SCL=1 时 SDA 上升沿
        if scl == 1 and prev_scl == 1 and sda == 1 and prev_sda == 0:
            self.log.append((tick, "STOP"))
            self._state = self._IDLE
            return

        # SCL 上升沿：采样 SDA
        if scl == 1 and prev_scl == 0:
            self._on_scl_rising(sda, tick)

        # SCL 下降沿：读模式下推进 tx_bit_index
        if scl == 0 and prev_scl == 1:
            self._on_scl_falling()

    def _on_scl_rising(self, sda: int, tick: int) -> None:
        """在 SCL 上升沿对 SDA 进行采样并推进状态机。"""
        if self._state in (self._IDLE,):
            return

        if self._state in (self._ADDR, self._DATA):
            if self._is_read and self._state == self._DATA:
                # 读模式：主机在 SCL 高期间采样我们输出的 SDA
                self._bit_count += 1
                if self._bit_count == 8:
                    self._byte_received(tick)
            else:
                # 写模式 / ADDR 阶段：采样 SDA 移入移位寄存器
                self._shift = ((self._shift << 1) | sda) & 0xFF
                self._bit_count += 1
                if self._bit_count == 8:
                    self._byte_received(tick)

        elif self._state in (self._ACK1, self._ACK2):
            # ACK 位本身（由从设备驱动，这里忽略电平，直接推进）
            if self._state == self._ACK1:
                # 进入数据阶段
                if self._is_read:
                    # 准备发送数据
                    self._load_tx_byte()
                self._state = self._DATA
            else:
                self._state = self._DATA
            self._shift = 0
            self._bit_count = 0

    def _on_scl_falling(self) -> None:
        """在 SCL 下降沿推进读模式的 tx_bit_index（准备下一位输出）。"""
        if self._state != self._DATA or not self._is_read:
            return
        # 只有在当前字节已采样至少 1 位后才推进（避免 ACK 时钟误触发）
        if self._bit_count == 0:
            return
        if self._tx_bit_index > 0:
            self._tx_bit_index -= 1

    def _byte_received(self, tick: int) -> None:
        byte = self._shift & 0xFF
        self._shift = 0
        self._bit_count = 0

        if self._state == self._ADDR:
            self._addr_byte = byte
            self._is_read = bool(byte & 0x01)
            direction = "R" if self._is_read else "W"
            addr7 = byte >> 1
            self.log.append((tick, f"{direction}:0x{addr7:02X}"))
            self._byte_index = 0
            self._state = self._ACK1
        else:  # DATA
            if not self._is_read:
                # 写操作：记录主机发来的字节
                self.log.append((tick, f"W_DATA:0x{byte:02X}"))
            else:
                # 读操作：记录主机读走的字节（即我们发送的）
                self.log.append((tick, f"R_DATA:0x{self._tx_current:02X}"))
                self._load_tx_byte()  # 准备下一字节
            self._byte_index += 1
            self._state = self._ACK2

    def _load_tx_byte(self) -> None:
        """从 tx_queue 取出下一个待发字节；队列空时发 0xFF。"""
        if self._tx_queue:
            self._tx_current = self._tx_queue.popleft()
        else:
            self._tx_current = 0xFF
        self._tx_bit_index = 7

    def get_sda_output(self) -> int | None:
        """CPU 读 P3.7 时调用；返回当前 SDA 输出位，或 None 表示不注入（使用实际引脚值）。"""
        if not self._is_read or self._state != self._DATA:
            return None  # 非读模式或非数据阶段：不干预 CPU 驱动的 SDA
        return (self._tx_current >> self._tx_bit_index) & 1


def _I2CBus_start(bus: I2CBus) -> int:
    bus._shift = 0
    bus._bit_count = 0
    bus._byte_index = 0
    return I2CBus._ADDR


# ---------------------------------------------------------------------------
# SPIBus
# ---------------------------------------------------------------------------

class SPIBus:
    """模拟 SPI 主机端时序解析（CS=P1.7, SCK=P1.4, MOSI=P1.5, MISO=P1.6）。"""

    def __init__(self) -> None:
        self.log: list[tuple[int, str]] = []
        self._tx_queue: deque[int] = deque()
        self.reset()

    def reset(self) -> None:
        self._active = False      # CS 是否有效（低电平）
        self._shift_in = 0        # 从 MOSI 移入的数据
        self._bit_count = 0       # 当前字节已收到多少位
        self._tx_current = 0xFF   # 当前 MISO 输出字节
        self._tx_bit_index = 7    # 当前输出位索引
        self._prev_cs = 1
        self._prev_sck = 0
        self.log.clear()
        self._tx_queue.clear()

    @property
    def has_pending(self) -> bool:
        return len(self._tx_queue) > 0

    def inject_response(self, data: bytes) -> None:
        """向 tx_queue 压入要通过 MISO 回复的字节序列。"""
        for b in data:
            self._tx_queue.append(b)

    def on_port_write(self, port: int, old_val: int, new_val: int, tick: int) -> None:
        """CPU 写端口时调用；只关心 P1（0x90）。"""
        if port != _SPI_PORT:
            return

        cs = (new_val >> _CS_BIT) & 1
        sck = (new_val >> _SCK_BIT) & 1
        mosi = (new_val >> _MOSI_BIT) & 1
        prev_cs = self._prev_cs
        prev_sck = self._prev_sck
        self._prev_cs = cs
        self._prev_sck = sck

        # CS 下降沿：帧起始
        if cs == 0 and prev_cs == 1:
            self._active = True
            self._shift_in = 0
            self._bit_count = 0
            self._load_tx_byte()
            return

        # CS 上升沿：帧结束
        if cs == 1 and prev_cs == 0:
            self._active = False
            return

        if not self._active:
            return

        # SCK 上升沿：采样 MOSI
        if sck == 1 and prev_sck == 0:
            self._shift_in = ((self._shift_in << 1) | mosi) & 0xFF
            self._bit_count += 1
            if self._bit_count == 8:
                rx_byte = self._shift_in
                tx_byte = self._tx_current
                self.log.append((tick, f"TX=0x{rx_byte:02X} RX=0x{tx_byte:02X}"))
                self._shift_in = 0
                self._bit_count = 0
                self._load_tx_byte()

        # SCK 下降沿：推进 MISO 位索引（准备下一位，在下次 SCK 上升沿前输出）
        if sck == 0 and prev_sck == 1:
            if self._active and self._bit_count > 0 and self._tx_bit_index > 0:
                self._tx_bit_index -= 1

    def inject_miso_into_port(self, port_val: int) -> int:
        """CPU 读 P1 时调用；将 MISO 位（bit6）替换为 tx_queue 当前位。"""
        if not self._active:
            return port_val
        bit = (self._tx_current >> self._tx_bit_index) & 1
        if bit:
            return port_val | (1 << _MISO_BIT)
        else:
            return port_val & ~(1 << _MISO_BIT)

    def _load_tx_byte(self) -> None:
        """从 tx_queue 取出下一个待发字节；队列空时发 0xFF。"""
        if self._tx_queue:
            self._tx_current = self._tx_queue.popleft()
        else:
            self._tx_current = 0xFF
        self._tx_bit_index = 7
