# 项目摘要（领导版）

## 一句话说明

这是一个面向 8051 / MCS-51 的最小开发与功能级仿真环境，用于在没有真实板卡、板卡资源紧张，或需要快速回归时，提前进行固件加载、逻辑调试和问题定位。

## 当前价值

- 降低对真实硬件的依赖，减少“等板子、占板子”的时间成本。
- 让固件逻辑、串口交互、定时器/中断流程可以在开发早期就开始联调。
- 支持脚本化回归测试，适合把典型场景沉淀成可重复验证的用例。
- 自带最小汇编构建、调试和追踪能力，便于定位问题而不是只“跑起来”。

## 已完成能力

- 加载并执行 `.asm`、`.hex`、`.bin`。
- 自带最小汇编器，可输出 `.bin`、`.hex`、`.sym.json`。
- 支持 8051 常用 CPU 指令、IRAM、SFR、位寻址、栈、DPTR、寄存器组。
- 支持 Timer0 / Timer1 的常用模式，以及 Timer0 mode 3 split timer。
- 支持外部中断、定时器中断、串口收发。
- 支持 runtime JSON 事件脚本，可按 tick 注入串口或外部事件。
- 支持 project JSON 工程配置，可保存一次完整运行参数。
- 支持断点、watchpoint、inspect、内存 dump、指令 trace。
- 支持 bit-bang I2C / SPI 时序解析与虚拟从设备响应注入。
- 提供命令行入口和 `tkinter` GUI 入口。

## 当前定位

这是“功能级仿真器”，适合做固件联调、逻辑验证和问题复现。

## 当前边界

- 不是 cycle-accurate 硬件级仿真器。
- 目前不追求精确机器周期、精确波特率和完整外设时序细节。
- 目前覆盖的是常用 8051 指令与常见开发场景，不是所有 8051 变种全覆盖。

## 当前验证结果

- 验证日期：2026-03-23
- 本地执行命令：`python -m unittest discover -s tests -v`
- 结果：34 个测试全部通过
- 已覆盖范围：装载、汇编、断点/watch、trace、串口、中断、定时器、I2C、SPI、project/runtime 配置等主路径

## 推荐演示内容

### 演示 1：基础能力

命令：

```bash
python -m mcs51 examples/hello_uart.asm
```

演示点：

- 能直接加载汇编并运行
- 能看到串口输出
- 能证明核心执行链路是通的

### 演示 2：调试能力

命令：

```bash
python -m mcs51 --project examples/echo_timer_demo.debug.project.json
```

演示点：

- 定时器与中断在运行
- 可配断点、watch、inspect、trace
- 更接近真实固件联调场景

### 演示 3：外设能力

命令：

```bash
python -m mcs51 --project examples/i2c_master.project.json
python -m mcs51 --project examples/spi_master.project.json
```

演示点：

- 能识别 bit-bang I2C / SPI 事务
- 能注入虚拟从设备响应
- 能把结果回传到 MCU 程序并输出到串口

## 仓库关键入口

- `README.md`：完整功能说明和使用方式
- `mcs51/cli.py`：命令行主入口
- `mcs51/cpu.py`：CPU 与执行主循环
- `mcs51/assembler.py`：最小汇编器
- `mcs51/debug.py`：断点、watch、trace
- `mcs51/peripheral.py`：I2C / SPI 外设模拟
- `tests/test_emulator.py`：主回归测试

## 适合对上汇报的结论

项目已经具备“可演示、可运行、可回归”的基础交付形态。当前最适合的定位是：作为 51 固件开发过程中的辅助调试与验证工具，先服务研发效率，再逐步扩展仿真精度和外设覆盖面。
