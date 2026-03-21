# 51 单片机模拟器

这是一个自写的 8051 / MCS-51 最小开发与仿真环境，目标不是只“把 `hex/bin` 跑起来”，而是尽量接近真实的 51 开发调试流。

当前已经支持：

- 加载并执行 `.asm`、`.hex`、`.bin`
- 自带最小汇编器，构建输出 `.bin/.hex/.sym.json`
- 8051 CPU 常用指令、IRAM、SFR、位寻址、栈、DPTR、寄存器组
- Timer0/Timer1 的 mode 0/1/2，外加 Timer0 mode 3 split timer
- 外部中断、定时器中断、串口中断
- 外部中断引脚高/低电平模型，支持边沿触发和电平触发
- 串口发送捕获、串口接收队列注入
- runtime JSON 事件脚本
- project JSON 工程配置
- 标签断点、单步、inspect、direct/XRAM dump
- watchpoint 停机、watch 日志、bit 上升沿/下降沿触发
- 指令 trace，带 `ASM=` 反汇编、标签和源码行
- `INCLUDE` 多文件装配
- 简单 `MACRO/ENDM` 宏定义与参数替换
- I2C / SPI bit-bang 时序解析与虚拟从设备响应注入

本轮又补了一批更常用的经典 8051 汇编指令与寻址形式，包括：

- `AJMP` / `ACALL`
- `JC` / `JNC` / `JB` / `JBC` / `DJNZ`
- `CJNE` 的 `A,direct`、`@Ri,#imm`、`Rn,#imm`
- `ADD` / `ADDC` / `SUBB` / `ORL` / `ANL` / `XRL`
- `MOV` 的 `A/@Ri/Rn/direct/bit/C/DPTR` 常见组合
- `MOVX` / `MOVC`
- `XCH` / `XCHD`
- `RR` / `RL` / `RRC` / `RLC` / `SWAP` / `DA`
- `MUL AB` / `DIV AB`

## 当前定位

这是一个“功能级仿真器”，不是 cycle accurate 模拟器。

已经做的部分偏向固件开发联调：

- 跑固件
- 看串口输出
- 注入串口输入和外部事件
- 看定时器和中断行为
- 用断点、watch、trace 排查逻辑问题

还没有做的部分主要是硬件时序精度：

- 精确机器周期和波特率时序
- 定时器 `C/T=1`
- 更多 8051 变种外设

## 安装

```bash
python -m pip install -e .
```

可用入口：

- `python -m mcs51`
- `python -m mcs51.build`
- `python -m mcs51.gui`
- `mcs51-sim`
- `mcs51-build`
- `mcs51-gui`

## 桌面软件（GUI）

现在提供了一个基于 `tkinter` 的桌面前端，适合不想手敲长命令时使用。

启动方式：

```bash
python -m mcs51.gui
```

或在安装后直接运行：

```bash
mcs51-gui
```

GUI 当前支持的常用流程：

- 选择 `ASM/HEX/BIN/project.json`
- 直接编译 `.asm`
- 直接运行模拟
- 注入串口输入
- 配置 `breakpoint / watch / inspect / trace`
- 查看控制台输出与调试结果

如果当前 Python 缺少 `tkinter`，GUI 无法启动；这种情况下可以继续使用命令行入口。

## 目录

- `mcs51/` 核心代码
- `examples/` 示例固件、构建产物、runtime、project
- `tests/` 回归测试
- `tools/build_examples.py` 批量构建示例

## 典型开发流

### 1. 编写汇编

示例：

- `examples/hello_uart.asm`
- `examples/echo_timer_demo.asm`
- `examples/multi_file_demo.asm`

### 2. 构建

```bash
python -m mcs51.build examples/echo_timer_demo.asm
```

默认输出：

- `examples/echo_timer_demo.bin`
- `examples/echo_timer_demo.hex`
- `examples/echo_timer_demo.sym.json`

批量构建所有示例：

```bash
python tools/build_examples.py
```

### 2.1 多文件与宏

现在可以用 `INCLUDE` 把多个汇编文件拼成一个工程，也可以定义简单宏。

`INCLUDE` 示例：

```asm
INCLUDE "lib_uart.inc"
```

宏定义示例：

```asm
PRINT_CHAR MACRO value
    MOV A,#value
    MOV SBUF,A
ENDM
```

宏调用示例：

```asm
PRINT_CHAR 'A'
```

当前宏能力定位为“简单可用”：

- 支持带参数宏
- 支持宏定义写在被 `INCLUDE` 的文件里
- 支持在主文件中调用 include 进来的宏
- 不支持嵌套 `MACRO`
- 不支持宏内局部标签自动改名

### 3. 运行

直接跑汇编：

```bash
python -m mcs51 examples/hello_uart.asm
```

跑 HEX：

```bash
python -m mcs51 examples/echo_timer_demo.hex
```

跑 BIN：

```bash
python -m mcs51 examples/echo_timer_demo.bin --format bin --origin 0x0000
```

## 串口输入

注入文本：

```bash
python -m mcs51 app.asm --serial-input "AT+RST\r\n"
```

注入十六进制字节：

```bash
python -m mcs51 app.asm --serial-input-hex "41 54 0D 0A"
```

说明：

- 程序把 `REN` 置 1 后，模拟器才会把输入字节送进串口接收缓冲并置 `RI`
- 程序写 `SBUF` 时，模拟器会立即记录输出并置 `TI`

## I2C / SPI 虚拟总线

模拟器内置 bit-bang I2C 和 SPI 协议解析，可自动识别程序操作端口引脚的时序并解帧。

### 固定引脚映射

| 协议 | 信号 | 引脚 | SFR  | 位  |
| ---- | ---- | ---- | ---- | --- |
| I2C  | SCL  | P3.6 | 0xB0 | 6   |
| I2C  | SDA  | P3.7 | 0xB0 | 7   |
| SPI  | SCK  | P1.4 | 0x90 | 4   |
| SPI  | MOSI | P1.5 | 0x90 | 5   |
| SPI  | MISO | P1.6 | 0x90 | 6   |
| SPI  | CS   | P1.7 | 0x90 | 7   |

### 注入虚拟从设备响应

在 runtime JSON 中用 `i2c_response` / `spi_response` 事件预置从设备回复数据：

```json
{
  "events": [
    { "tick": 0, "type": "i2c_response", "hex": "55" },
    { "tick": 0, "type": "spi_response", "hex": "55" }
  ]
}
```

### 查看事务日志

```bash
# I2C 日志
python -m mcs51 --project examples/i2c_master.project.json --trace-i2c

# SPI 日志
python -m mcs51 --project examples/spi_master.project.json --trace-spi
```

也可在 project JSON 中设置 `"trace_i2c": true` / `"trace_spi": true`。

## Runtime 事件脚本

runtime JSON 可以在指定 tick 注入事件，适合模拟串口输入或外部中断。

示例文件：

- `examples/echo_timer_demo.runtime.json`

运行：

```bash
python -m mcs51 examples/echo_timer_demo.asm --runtime examples/echo_timer_demo.runtime.json --max-instructions 512
```

当前支持事件类型：

- `serial`
- `extint0`
- `extint1`
- `extint0_low`
- `extint0_high`
- `extint1_low`
- `extint1_high`
- `i2c_response`：向 I2C 虚拟从设备注入回复字节（`hex` 字段）
- `spi_response`：向 SPI 虚拟从设备注入 MISO 字节（`hex` 字段）

格式示例：

```json
{
  "events": [
    { "tick": 96, "type": "serial", "text": "AB\r" },
    { "tick": 128, "type": "extint0_low" },
    { "tick": 132, "type": "extint0_high" },
    { "tick": 0, "type": "i2c_response", "hex": "55" },
    { "tick": 0, "type": "spi_response", "hex": "55" }
  ]
}
```

## Project 工程文件

project JSON 用来保存一次完整的运行配置。

示例：

- `examples/echo_timer_demo.project.json`
- `examples/echo_timer_demo.debug.project.json`

运行：

```bash
python -m mcs51 --project examples/echo_timer_demo.debug.project.json
```

工程文件支持保存：

- 镜像路径、格式、入口地址
- runtime / symbols / trace 文件
- breakpoints / watchpoints / inspect
- `watch_log`
- step limit
- direct / XRAM dump
- trace / interrupt / port 输出选项
- `trace_i2c` / `trace_spi`：启用 I2C / SPI 事务日志

## 调试能力

### 符号与断点

先构建出符号文件：

```bash
python -m mcs51.build examples/echo_timer_demo.asm
```

按标签断点：

```bash
python -m mcs51 examples/echo_timer_demo.hex --symbols examples/echo_timer_demo.sym.json --breakpoint main_loop
```

列出符号：

```bash
python -m mcs51 examples/echo_timer_demo.hex --symbols examples/echo_timer_demo.sym.json --list-symbols
```

### 单步 / Inspect / Dump

```bash
python -m mcs51 examples/echo_timer_demo.asm --step 6 --inspect TMOD --inspect TR0 --dump-direct 0x88:2
```

### Watchpoints

默认 watch 会在值变化时停机：

```bash
python -m mcs51 examples/echo_timer_demo.asm --watch 30H --max-instructions 128
```

watch 目标支持：

- 直接地址，如 `30H`
- SFR，如 `TMOD`
- 位地址或位符号，如 `TR0`、`P1.0`
- XRAM，如 `xram:0x1000`

watch 前缀支持：

- `log:` 记录事件但不中断执行
- `stop:` 显式指定命中后停机
- `rise:` 仅 bit 从 `0 -> 1` 触发
- `fall:` 仅 bit 从 `1 -> 0` 触发

可以组合使用，前缀顺序不限：

```bash
python -m mcs51 examples/echo_timer_demo.asm --watch log:TMOD --watch log:rise:TR0 --watch-log --step 6
```

### Watch 日志

加 `--watch-log` 后，结束时会输出 watch 事件历史，包括：

- 指令序号
- PC
- 变化说明
- 对应 `ASM=` 反汇编
- 标签
- 源码行

### 指令 Trace

```bash
python -m mcs51 examples/echo_timer_demo.asm --trace-file trace.txt --trace-limit 64 --max-instructions 128
```

trace 现在包含：

- 指令序号
- `PC`
- `OP`
- `ASM`
- 关键寄存器
- 标签
- 源码行

即使是没有源码映射的裸 `bin`，也会输出 `ASM=` 反汇编文本。

## 常用参数

- `--project path/to/project.json`
- `--format auto|hex|bin|asm`
- `--origin 0x0000`
- `--entry 0x0000`
- `--max-instructions 100000`
- `--serial-input "text"`
- `--serial-input-hex "41 42 0D 0A"`
- `--runtime path/to/runtime.json`
- `--symbols path/to/file.sym.json`
- `--breakpoint label_or_address`
- `--watch target`
- `--watch-log`
- `--inspect target`
- `--step N`
- `--dump-direct start:length`
- `--dump-xram start:length`
- `--trace-file trace.txt`
- `--trace-limit 128`
- `--list-symbols`
- `--trace-ports`
- `--trace-interrupts`
- `--trace-i2c`
- `--trace-spi`
- `--dump-iram`
- `--dump-sfr`
- `--no-tight-loop-detect`

## 示例说明

### hello_uart

`examples/hello_uart.asm` 会输出：

```text
HELLO 51
```

然后进入自旋。

### echo_timer_demo

`examples/echo_timer_demo.asm` 更接近真实固件：

- `Timer0` mode 2 自动重装
- Timer0 ISR 翻转 `P1.0`
- Timer0 ISR 递增 `IRAM[30H]`
- 主循环接收串口字符并回显
- 收到回车后输出 `CRLF`

这个示例不会自然停机，因为定时器和中断会持续运行，通常需要配合 `--max-instructions`、断点或 watch 使用。

### multi_file_demo

`examples/multi_file_demo.asm` 演示了：

- 主文件 `INCLUDE "lib_uart.inc"`
- include 文件里同时放宏和子程序
- 主文件调用 include 进来的 `send_banner`
- 主文件调用 include 进来的 `PRINT_CHAR` 宏

### i2c_master

`examples/i2c_master.asm` 演示 bit-bang I2C：

- `P3.6` = SCL，`P3.7` = SDA
- 向地址 `0x27` 写 `0xAB`，再发 Repeated START 读 1 字节
- 通过 runtime 注入虚拟从设备回复 `0x55`，程序读回后输出到串口

运行：

```bash
python -m mcs51 --project examples/i2c_master.project.json
```

预期串口输出 `U`（0x55），I2C log：

```
W:0x27 W_DATA:0xAB → R:0x27 R_DATA:0x55
```

### spi_master

`examples/spi_master.asm` 演示 bit-bang SPI（Mode 0）：

- `P1.7` = CS，`P1.4` = SCK，`P1.5` = MOSI，`P1.6` = MISO
- 发送 `0xAB`，同时读回 1 字节
- 通过 runtime 注入虚拟从设备回复 `0x55`，程序读回后输出到串口

运行：

```bash
python -m mcs51 --project examples/spi_master.project.json
```

预期串口输出 `U`（0x55），SPI log：

```
TX=0xAB RX=0x55
```

## 测试

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖：

- `HEX/BIN/ASM` 三种加载路径
- 汇编器与构建输出
- 多文件 `INCLUDE`
- 宏定义与宏展开
- 符号文件
- 标签断点
- watch 停机与 watch 日志
- bit 上升沿 / 下降沿 watch
- 单步 / inspect / dump
- project 文件运行
- trace 文件与 `ASM=` 反汇编
- 串口收发
- runtime 事件注入
- 边沿触发 / 电平触发外部中断
- Timer0 mode 3 split timer
- `LCALL/RET`
- `RETI`
- Timer0 中断流
