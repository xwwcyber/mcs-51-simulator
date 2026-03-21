# GUI 使用说明

这个文件说明当前桌面界面 `mcs51.gui` 怎么使用。

主界面代码在：

- `mcs51/gui.py`

## 1. 启动 GUI

在项目根目录运行：

```bash
python -m mcs51.gui
```

默认使用 Sun Valley 主题（需要 `pip install sv-ttk`）。

如果想用经典原版界面：

```bash
python -m mcs51.gui --classic
```

如果你在 Git Bash 里启动，也可以用：

```bash
pythonw -m mcs51.gui &
```

启动后会打开一个名为 `MCS-51 Simulator GUI` 的窗口。

## 2. 界面结构

窗口主要分成两部分：

- 上半部分：功能区
- 下半部分：执行输出

现在上半部分支持：

- 右侧滚动条
- 鼠标滚轮滚动

功能区里有两个标签页：

- `运行`
- `编译`

### 2.1 运行页

`运行` 页用来直接运行 `.asm`、`.hex`、`.bin` 或 `project.json`。

主要区域：

- 示例按钮
  - `hello_uart`
  - `echo_timer_demo`
  - `清空运行参数`
- 路径
  - `Project`
  - `Image`
  - `Runtime`
  - `Symbols`
  - `Trace`
- 基础运行参数
  - `Format`
  - `Origin`
  - `Entry`
  - `Max Instructions`
  - `Step`
  - `Trace Limit`
  - `Serial Input`
  - `Serial Hex`
- 调试参数
  - `Breakpoint`
  - `Watch`
  - `Inspect`
  - `Dump Direct`
  - `Dump XRAM`
- 输出选项
  - `Watch Log`
  - `List Symbols`
  - `Trace Ports`
  - `Trace Interrupts`
  - `Dump IRAM`
  - `Dump SFR`
  - `Tight Loop Detect`
- 按钮
  - `运行模拟器`
  - `仅列出符号`
  - `清空输出`

### 2.2 编译页

`编译` 页用来把 `.asm` 编译成：

- `.bin`
- `.hex`
- `.sym.json`

主要区域：

- `Source ASM`
- `BIN Out`
- `HEX Out`
- `SYM Out`
- `编译 ASM`
- `把 Source 复制到 Run`

## 3. 最快上手

### 3.1 跑内置示例

最简单的方式：

1. 打开 `运行` 页
2. 点击 `hello_uart`
3. 点击 `运行模拟器`

执行结束后，下方输出区会出现类似内容：

```text
Loaded ASM image ...
Serial output:
HELLO 51
```

如果要跑更完整的示例：

1. 点击 `echo_timer_demo`
2. 点击 `运行模拟器`

这个示例会带定时器、中断和串口行为。

### 3.2 跑自己的程序

1. 打开 `运行` 页
2. 在 `Image` 右边点 `浏览`
3. 选择你的文件

支持：

- `.asm`
- `.hex`
- `.bin`

然后：

- 如果是 `.asm`，一般直接点 `运行模拟器`
- 如果是 `.hex`，一般直接点 `运行模拟器`
- 如果是 `.bin`，通常还要填写 `Origin`，例如 `0x0000`

## 4. 先编译再运行

如果你想先把汇编编译出来，再运行：

1. 打开 `编译` 页
2. 在 `Source ASM` 选择你的 `.asm`
3. 如果需要，自定义 `BIN Out`、`HEX Out`、`SYM Out`
4. 点击 `编译 ASM`
5. 点击 `把 Source 复制到 Run`
6. 切回 `运行` 页
7. 点击 `运行模拟器`

说明：

- 如果不手动填输出路径，构建逻辑会按默认规则输出
- `SYM` 文件用于符号、断点、调试显示

## 5. 常用输入项说明

### 5.1 路径类

- `Project`
  - 选择 `project.json`
  - 适合一次保存完整运行配置
- `Image`
  - 选择 `.asm`、`.hex` 或 `.bin`
- `Runtime`
  - 选择 runtime 事件 JSON
  - 用于定时注入串口或外部中断
- `Symbols`
  - 选择 `.sym.json`
  - 用于标签断点、符号显示、源码映射
- `Trace`
  - 指定 trace 文本输出文件

### 5.2 基础运行参数

- `Format`
  - `auto / asm / hex / bin`
  - 正常情况下选 `auto` 就够了
- `Origin`
  - 主要给 `.bin` 用
  - 例：`0x0000`
- `Entry`
  - 覆盖默认入口地址
- `Max Instructions`
  - 最多执行多少条指令
  - 程序可能有死循环，建议保留这个限制
- `Step`
  - 只执行固定条数
- `Trace Limit`
  - 限制 trace 文件记录的指令数
- `Serial Input`
  - 注入普通文本到串口接收
  - 例：`AB\r`
- `Serial Hex`
  - 注入十六进制字节
  - 例：`41 42 0D`

### 5.3 调试参数

- `Breakpoint`
  - 断点，支持标签或地址
  - 多个值可用逗号或换行分隔
- `Watch`
  - 监视目标
  - 例：`30H`
  - 例：`log:rise:TR0`
- `Inspect`
  - 程序结束后查看值
  - 例：`TMOD,TR0`
- `Dump Direct`
  - 查看 direct 地址范围
  - 例：`0x88:2`
- `Dump XRAM`
  - 查看 XRAM 地址范围
  - 例：`0x1000:16`

## 6. 常见操作

### 6.1 查看程序输出

点击 `运行模拟器` 后，结果会显示在下方“执行输出”区域。

重点看这些内容：

- `Halt reason`
- `Instructions`
- `Ticks`
- `Serial output`
- `Registers`
- `Ports`

### 6.2 只看符号表

如果你已经有 `.sym.json`，可以：

1. 选择 `Image`
2. 选择 `Symbols`
3. 点击 `仅列出符号`

### 6.3 导出 trace

1. 在 `Trace` 里选一个输出文件
2. 填 `Trace Limit`
3. 点击 `运行模拟器`

程序执行后会把 trace 写到那个文本文件里。

## 7. 两个推荐流程

### 7.1 只想快速看程序能不能跑

1. 选 `Image`
2. 填 `Max Instructions`
3. 点 `运行模拟器`

### 7.2 想查中断、定时器或变量变化

1. 选 `Image`
2. 选 `Symbols`
3. 填 `Watch`
4. 填 `Inspect`
5. 需要时勾选 `Trace Interrupts` 和 `Watch Log`
6. 点 `运行模拟器`

## 8. 常见问题

### 8.1 点了运行没有反应

先看窗口右上角状态文字是否变成：

- `运行模拟器中...`
- `运行模拟器完成`
- `运行模拟器失败(...)`

如果失败，错误信息会显示在下方输出区。

### 8.2 程序一直跑不完

这是正常情况，很多单片机程序本来就会常驻循环。

解决方式：

- 设置 `Max Instructions`
- 使用 `Step`
- 使用 `Breakpoint`
- 使用 `Watch`

### 8.3 BIN 跑不起来

通常是因为没有设置正确的 `Origin`。

### 8.4 看不到想要的调试信息

检查是否已经：

- 提供了 `Symbols`
- 勾选了对应输出选项
- 填写了 `Watch` / `Inspect` / `Trace`

## 9. 推荐的第一步

如果你第一次用这个 GUI，建议先按下面做：

1. 打开 GUI
2. 点击 `hello_uart`
3. 点击 `运行模拟器`
4. 确认输出里出现 `HELLO 51`

跑通以后，再试：

1. 点击 `echo_timer_demo`
2. 点击 `运行模拟器`
3. 观察串口输出、寄存器和中断信息
