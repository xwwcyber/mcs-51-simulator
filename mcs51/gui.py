from __future__ import annotations

import contextlib
import io
import queue
import subprocess
import threading
import traceback
import sys
from dataclasses import dataclass, replace
from typing import Callable

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:  # pragma: no cover - depends on local Python install
    tk = None
    filedialog = None
    messagebox = None
    ttk = None
    ScrolledText = None

from .build import main as build_main
from .cli import main as cli_main


def _clean_text(value: str) -> str | None:
    text = value.strip()
    return text or None


def _split_multi_value(value: str) -> list[str]:
    normalized = value.replace(",", "\n").replace(";", "\n")
    return [item.strip() for item in normalized.splitlines() if item.strip()]


def _format_command(module_name: str, argv: list[str]) -> str:
    rendered = subprocess.list2cmdline(argv)
    if rendered:
        return f"python -m {module_name} {rendered}"
    return f"python -m {module_name}"


@dataclass(frozen=True)
class RunCommandConfig:
    project: str | None = None
    image: str | None = None
    fmt: str | None = None
    origin: str | None = None
    entry: str | None = None
    max_instructions: str | None = None
    serial_input: str | None = None
    serial_input_hex: str | None = None
    runtime: str | None = None
    symbols: str | None = None
    breakpoints: tuple[str, ...] = ()
    watchpoints: tuple[str, ...] = ()
    inspect: tuple[str, ...] = ()
    step: str | None = None
    dump_direct: tuple[str, ...] = ()
    dump_xram: tuple[str, ...] = ()
    trace_file: str | None = None
    trace_limit: str | None = None
    watch_log: bool = False
    list_symbols: bool = False
    trace_ports: bool = False
    trace_interrupts: bool = False
    dump_iram: bool = False
    dump_sfr: bool = False
    tight_loop_detect: bool = True


@dataclass(frozen=True)
class BuildCommandConfig:
    source: str
    bin_out: str | None = None
    hex_out: str | None = None
    sym_out: str | None = None


@dataclass(frozen=True)
class CommandResult:
    title: str
    command: str
    exit_code: int
    output: str


def build_run_argv(config: RunCommandConfig) -> list[str]:
    argv: list[str] = []

    if config.project:
        argv.extend(["--project", config.project])
    if config.image:
        argv.append(config.image)
    if not config.project and not config.image:
        raise ValueError("请至少选择 image 或 project。")
    if config.fmt:
        argv.extend(["--format", config.fmt])
    if config.origin:
        argv.extend(["--origin", config.origin])
    if config.entry:
        argv.extend(["--entry", config.entry])
    if config.max_instructions:
        argv.extend(["--max-instructions", config.max_instructions])
    if config.serial_input:
        argv.extend(["--serial-input", config.serial_input])
    if config.serial_input_hex:
        argv.extend(["--serial-input-hex", config.serial_input_hex])
    if config.runtime:
        argv.extend(["--runtime", config.runtime])
    if config.symbols:
        argv.extend(["--symbols", config.symbols])
    for breakpoint in config.breakpoints:
        argv.extend(["--breakpoint", breakpoint])
    for watch in config.watchpoints:
        argv.extend(["--watch", watch])
    if config.watch_log:
        argv.append("--watch-log")
    for inspect in config.inspect:
        argv.extend(["--inspect", inspect])
    if config.step:
        argv.extend(["--step", config.step])
    for spec in config.dump_direct:
        argv.extend(["--dump-direct", spec])
    for spec in config.dump_xram:
        argv.extend(["--dump-xram", spec])
    if config.trace_file:
        argv.extend(["--trace-file", config.trace_file])
    if config.trace_limit:
        argv.extend(["--trace-limit", config.trace_limit])
    if config.list_symbols:
        argv.append("--list-symbols")
    if config.trace_ports:
        argv.append("--trace-ports")
    if config.trace_interrupts:
        argv.append("--trace-interrupts")
    if config.dump_iram:
        argv.append("--dump-iram")
    if config.dump_sfr:
        argv.append("--dump-sfr")
    if not config.tight_loop_detect:
        argv.append("--no-tight-loop-detect")
    return argv


def build_build_argv(config: BuildCommandConfig) -> list[str]:
    argv = [config.source]
    if config.bin_out:
        argv.extend(["--bin-out", config.bin_out])
    if config.hex_out:
        argv.extend(["--hex-out", config.hex_out])
    if config.sym_out:
        argv.extend(["--sym-out", config.sym_out])
    return argv


def _invoke_entrypoint(
    title: str,
    module_name: str,
    entrypoint: Callable[[list[str] | None], int],
    argv: list[str],
) -> CommandResult:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exit_code = entrypoint(argv)
        except SystemExit as exc:  # pragma: no cover - argparse exits this way
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except Exception:
            exit_code = 1
            stderr.write("Unhandled exception while running command.\n")
            stderr.write(traceback.format_exc())

    combined = stdout.getvalue()
    if stderr.getvalue():
        if combined and not combined.endswith("\n"):
            combined += "\n"
        combined += stderr.getvalue()
    if not combined:
        combined = "<no output>\n"

    return CommandResult(
        title=title,
        command=_format_command(module_name, argv),
        exit_code=exit_code,
        output=combined,
    )


class EmulatorGui:
    def __init__(self, root: tk.Tk, *, classic: bool = False) -> None:
        self.root = root
        self.root.title("MCS-51 Simulator GUI")
        self.root.geometry("1280x820")
        self.root.minsize(900, 600)

        self._apply_theme(classic=classic)

        self.result_queue: queue.Queue[CommandResult] = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self.project_var = tk.StringVar()
        self.image_var = tk.StringVar()
        self.format_var = tk.StringVar(value="auto")
        self.origin_var = tk.StringVar()
        self.entry_var = tk.StringVar()
        self.max_instructions_var = tk.StringVar(value="512")
        self.serial_input_var = tk.StringVar()
        self.serial_input_hex_var = tk.StringVar()
        self.runtime_var = tk.StringVar()
        self.symbols_var = tk.StringVar()
        self.breakpoints_var = tk.StringVar()
        self.watchpoints_var = tk.StringVar()
        self.inspect_var = tk.StringVar()
        self.step_var = tk.StringVar()
        self.dump_direct_var = tk.StringVar()
        self.dump_xram_var = tk.StringVar()
        self.trace_file_var = tk.StringVar()
        self.trace_limit_var = tk.StringVar(value="64")
        self.watch_log_var = tk.BooleanVar(value=True)
        self.list_symbols_var = tk.BooleanVar(value=False)
        self.trace_ports_var = tk.BooleanVar(value=False)
        self.trace_interrupts_var = tk.BooleanVar(value=True)
        self.dump_iram_var = tk.BooleanVar(value=False)
        self.dump_sfr_var = tk.BooleanVar(value=False)
        self.tight_loop_detect_var = tk.BooleanVar(value=True)

        self.build_source_var = tk.StringVar()
        self.build_bin_var = tk.StringVar()
        self.build_hex_var = tk.StringVar()
        self.build_sym_var = tk.StringVar()

        self.status_var = tk.StringVar(value="就绪")
        self.run_preview_var = tk.StringVar(value="python -m mcs51")
        self.build_preview_var = tk.StringVar(value="python -m mcs51.build")
        self.action_buttons: list[ttk.Button] = []

        self._preview_pending: str | None = None

        self._build_ui()
        self._bind_preview_updates()
        self._bind_shortcuts()
        self._refresh_previews()
        self.root.after(120, self._poll_result_queue)
        self.root.after(50, self._set_initial_sash)

    def _set_initial_sash(self) -> None:
        self.root.update_idletasks()
        total = self._paned.winfo_height()
        if total > 1:
            self._paned.sashpos(0, total * 2 // 3)

    def _apply_theme(self, *, classic: bool = False) -> None:
        if not classic:
            try:
                import sv_ttk
                sv_ttk.set_theme("light")
            except ImportError:
                classic = True

        if classic:
            style = ttk.Style()
            available = style.theme_names()
            for theme in ("vista", "clam"):
                if theme in available:
                    style.theme_use(theme)
                    break
            style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
            style.map(
                "Accent.TButton",
                background=[("active", "#0060c0"), ("!disabled", "#0078d4")],
                foreground=[("!disabled", "white")],
            )

        style = ttk.Style()
        style.configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook.Tab", padding=(14, 6))

        style.configure("Status.TLabel", font=("Segoe UI", 9))

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)

        ttk.Label(
            header,
            text="MCS-51 Simulator",
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.grid(row=0, column=1, sticky="e")

        self._paned = ttk.PanedWindow(main, orient="vertical")
        self._paned.grid(row=1, column=0, sticky="nsew")

        top = ttk.Frame(self._paned, padding=4)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        self._paned.add(top, weight=2)

        bottom = ttk.Frame(self._paned, padding=4)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)
        self._paned.add(bottom, weight=1)

        notebook = ttk.Notebook(top)
        notebook.grid(row=0, column=0, sticky="nsew")

        run_canvas, run_tab = self._create_scrollable_tab(notebook, "运行")
        build_canvas, build_tab = self._create_scrollable_tab(notebook, "编译")

        self._build_run_tab(run_tab)
        self._build_build_tab(build_tab)
        self._bind_mousewheel_recursive(run_tab, run_canvas)
        self._bind_mousewheel_recursive(build_tab, build_canvas)
        self._build_output_panel(bottom)

    def _create_scrollable_tab(self, notebook: ttk.Notebook, title: str) -> tuple[tk.Canvas, ttk.Frame]:
        outer = ttk.Frame(notebook)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas, padding=12)
        body.columnconfigure(0, weight=1)

        window_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        def _update_scrollregion(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_body_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        body.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _sync_body_width)

        notebook.add(outer, text=title)
        return canvas, body

    def _scroll_canvas(self, canvas: tk.Canvas, event: tk.Event) -> str:
        if getattr(event, "delta", 0):
            steps = max(1, abs(event.delta) // 120)
            direction = -1 if event.delta > 0 else 1
        elif getattr(event, "num", None) == 4:
            steps = 1
            direction = -1
        elif getattr(event, "num", None) == 5:
            steps = 1
            direction = 1
        else:
            return "break"

        canvas.yview_scroll(direction * steps, "units")
        return "break"

    def _bind_mousewheel_recursive(self, widget: tk.Widget, canvas: tk.Canvas) -> None:
        widget.bind("<MouseWheel>", lambda event: self._scroll_canvas(canvas, event), add="+")
        widget.bind("<Button-4>", lambda event: self._scroll_canvas(canvas, event), add="+")
        widget.bind("<Button-5>", lambda event: self._scroll_canvas(canvas, event), add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child, canvas)

    def _build_run_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        shortcuts = ttk.Frame(parent)
        shortcuts.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(shortcuts, text="示例").pack(side="left")
        self._make_button(shortcuts, "hello_uart", self._load_hello_example).pack(side="left", padx=(8, 0))
        self._make_button(shortcuts, "echo_timer_demo", self._load_echo_example).pack(side="left", padx=6)
        self._make_button(shortcuts, "清空运行参数", self._reset_run_form).pack(side="left", padx=(18, 0))

        path_frame = ttk.LabelFrame(parent, text="路径")
        path_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        path_frame.columnconfigure(1, weight=1)

        self._add_path_row(path_frame, 0, "Project", self.project_var, self._browse_project)
        self._add_path_row(path_frame, 1, "Image", self.image_var, self._browse_image)
        self._add_path_row(path_frame, 2, "Runtime", self.runtime_var, self._browse_runtime)
        self._add_path_row(path_frame, 3, "Symbols", self.symbols_var, self._browse_symbols)
        self._add_path_row(path_frame, 4, "Trace", self.trace_file_var, self._browse_trace_output, save_dialog=True)

        settings = ttk.Frame(parent)
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        settings.columnconfigure(0, weight=1)
        settings.columnconfigure(1, weight=1)

        basic = ttk.LabelFrame(settings, text="基础运行参数")
        basic.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        basic.columnconfigure(1, weight=1)
        self._add_entry_row(basic, 0, "Format", self.format_var, width=18, values=("auto", "asm", "hex", "bin"))
        self._add_entry_row(basic, 1, "Origin", self.origin_var)
        self._add_entry_row(basic, 2, "Entry", self.entry_var)
        self._add_entry_row(basic, 3, "Max Instructions", self.max_instructions_var)
        self._add_entry_row(basic, 4, "Step", self.step_var)
        self._add_entry_row(basic, 5, "Trace Limit", self.trace_limit_var)
        self._add_entry_row(basic, 6, "Serial Input", self.serial_input_var)
        self._add_entry_row(basic, 7, "Serial Hex", self.serial_input_hex_var)

        debug = ttk.LabelFrame(settings, text="调试参数")
        debug.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        debug.columnconfigure(1, weight=1)
        self._add_entry_row(debug, 0, "Breakpoint", self.breakpoints_var, help_text="逗号或换行分隔")
        self._add_entry_row(debug, 1, "Watch", self.watchpoints_var, help_text="逗号或换行分隔")
        self._add_entry_row(debug, 2, "Inspect", self.inspect_var, help_text="逗号或换行分隔")
        self._add_entry_row(debug, 3, "Dump Direct", self.dump_direct_var, help_text="如 0x88:2")
        self._add_entry_row(debug, 4, "Dump XRAM", self.dump_xram_var, help_text="如 0x1000:16")

        toggles = ttk.LabelFrame(parent, text="输出选项")
        toggles.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        ttk.Checkbutton(toggles, text="Watch Log", variable=self.watch_log_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(toggles, text="List Symbols", variable=self.list_symbols_var).grid(row=0, column=1, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(toggles, text="Trace Ports", variable=self.trace_ports_var).grid(row=0, column=2, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(toggles, text="Trace Interrupts", variable=self.trace_interrupts_var).grid(row=0, column=3, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(toggles, text="Dump IRAM", variable=self.dump_iram_var).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(toggles, text="Dump SFR", variable=self.dump_sfr_var).grid(row=1, column=1, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(toggles, text="Tight Loop Detect", variable=self.tight_loop_detect_var).grid(row=1, column=2, sticky="w", padx=8, pady=6)

        actions = ttk.Frame(parent)
        actions.grid(row=4, column=0, sticky="nsew")
        actions.columnconfigure(0, weight=1)

        preview = ttk.LabelFrame(actions, text="命令预览")
        preview.grid(row=0, column=0, sticky="ew")
        preview.columnconfigure(0, weight=1)
        ttk.Label(preview, textvariable=self.run_preview_var, wraplength=980, justify="left").grid(
            row=0,
            column=0,
            sticky="ew",
            padx=8,
            pady=8,
        )

        buttons = ttk.Frame(actions)
        buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        run_btn = self._make_button(buttons, "运行模拟器 (F5)", self._run_simulator)
        run_btn.configure(style="Accent.TButton")
        run_btn.pack(side="left")
        self._make_button(buttons, "仅列出符号", self._run_list_symbols).pack(side="left", padx=6)
        self._make_button(buttons, "清空输出 (Ctrl+L)", self._clear_output).pack(side="left", padx=6)

    def _build_build_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        paths = ttk.LabelFrame(parent, text="编译输出")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        paths.columnconfigure(1, weight=1)

        self._add_path_row(paths, 0, "Source ASM", self.build_source_var, self._browse_build_source)
        self._add_path_row(paths, 1, "BIN Out", self.build_bin_var, self._browse_build_bin, save_dialog=True)
        self._add_path_row(paths, 2, "HEX Out", self.build_hex_var, self._browse_build_hex, save_dialog=True)
        self._add_path_row(paths, 3, "SYM Out", self.build_sym_var, self._browse_build_sym, save_dialog=True)

        preview = ttk.LabelFrame(parent, text="命令预览")
        preview.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        preview.columnconfigure(0, weight=1)
        ttk.Label(preview, textvariable=self.build_preview_var, wraplength=980, justify="left").grid(
            row=0,
            column=0,
            sticky="ew",
            padx=8,
            pady=8,
        )

        buttons = ttk.Frame(parent)
        buttons.grid(row=2, column=0, sticky="w")
        build_btn = self._make_button(buttons, "编译 ASM", self._build_source)
        build_btn.configure(style="Accent.TButton")
        build_btn.pack(side="left")
        self._make_button(buttons, "把 Source 复制到 Run", self._copy_build_source_to_run).pack(side="left", padx=6)

    def _build_output_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="执行输出", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.output_text = ScrolledText(
            parent,
            wrap="word",
            font=("Consolas", 10),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            selectbackground="#264f78",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=1,
            padx=8,
            pady=6,
        )
        self.output_text.grid(row=1, column=0, sticky="nsew")
        self.output_text.configure(state="disabled")

        self.output_text.tag_configure("divider", foreground="#555555")
        self.output_text.tag_configure("header", foreground="#569cd6", font=("Consolas", 10, "bold"))
        self.output_text.tag_configure("success", foreground="#4ec9b0")
        self.output_text.tag_configure("error", foreground="#f44747")
        self.output_text.tag_configure("output", foreground="#d4d4d4")

    def _bind_preview_updates(self) -> None:
        variables = [
            self.project_var,
            self.image_var,
            self.format_var,
            self.origin_var,
            self.entry_var,
            self.max_instructions_var,
            self.serial_input_var,
            self.serial_input_hex_var,
            self.runtime_var,
            self.symbols_var,
            self.breakpoints_var,
            self.watchpoints_var,
            self.inspect_var,
            self.step_var,
            self.dump_direct_var,
            self.dump_xram_var,
            self.trace_file_var,
            self.trace_limit_var,
            self.watch_log_var,
            self.list_symbols_var,
            self.trace_ports_var,
            self.trace_interrupts_var,
            self.dump_iram_var,
            self.dump_sfr_var,
            self.tight_loop_detect_var,
            self.build_source_var,
            self.build_bin_var,
            self.build_hex_var,
            self.build_sym_var,
        ]
        for variable in variables:
            variable.trace_add("write", lambda *_args: self._schedule_preview_refresh())

    def _schedule_preview_refresh(self) -> None:
        if self._preview_pending is not None:
            self.root.after_cancel(self._preview_pending)
        self._preview_pending = self.root.after(80, self._do_refresh_previews)

    def _do_refresh_previews(self) -> None:
        self._preview_pending = None
        self._refresh_previews()

    def _refresh_previews(self) -> None:
        try:
            run_argv = build_run_argv(self._collect_run_config())
            self.run_preview_var.set(_format_command("mcs51", run_argv))
        except ValueError:
            self.run_preview_var.set("python -m mcs51 <image 或 --project>")

        try:
            build_argv = build_build_argv(self._collect_build_config())
            self.build_preview_var.set(_format_command("mcs51.build", build_argv))
        except ValueError:
            self.build_preview_var.set("python -m mcs51.build <source.asm>")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<F5>", lambda _e: self._run_simulator())
        self.root.bind("<Control-l>", lambda _e: self._clear_output())
        self.root.bind("<Control-L>", lambda _e: self._clear_output())

    def _collect_run_config(self) -> RunCommandConfig:
        return RunCommandConfig(
            project=_clean_text(self.project_var.get()),
            image=_clean_text(self.image_var.get()),
            fmt=_clean_text(self.format_var.get()),
            origin=_clean_text(self.origin_var.get()),
            entry=_clean_text(self.entry_var.get()),
            max_instructions=_clean_text(self.max_instructions_var.get()),
            serial_input=_clean_text(self.serial_input_var.get()),
            serial_input_hex=_clean_text(self.serial_input_hex_var.get()),
            runtime=_clean_text(self.runtime_var.get()),
            symbols=_clean_text(self.symbols_var.get()),
            breakpoints=tuple(_split_multi_value(self.breakpoints_var.get())),
            watchpoints=tuple(_split_multi_value(self.watchpoints_var.get())),
            inspect=tuple(_split_multi_value(self.inspect_var.get())),
            step=_clean_text(self.step_var.get()),
            dump_direct=tuple(_split_multi_value(self.dump_direct_var.get())),
            dump_xram=tuple(_split_multi_value(self.dump_xram_var.get())),
            trace_file=_clean_text(self.trace_file_var.get()),
            trace_limit=_clean_text(self.trace_limit_var.get()),
            watch_log=self.watch_log_var.get(),
            list_symbols=self.list_symbols_var.get(),
            trace_ports=self.trace_ports_var.get(),
            trace_interrupts=self.trace_interrupts_var.get(),
            dump_iram=self.dump_iram_var.get(),
            dump_sfr=self.dump_sfr_var.get(),
            tight_loop_detect=self.tight_loop_detect_var.get(),
        )

    def _collect_build_config(self) -> BuildCommandConfig:
        source = _clean_text(self.build_source_var.get())
        if not source:
            raise ValueError("请先选择 ASM 源文件。")
        return BuildCommandConfig(
            source=source,
            bin_out=_clean_text(self.build_bin_var.get()),
            hex_out=_clean_text(self.build_hex_var.get()),
            sym_out=_clean_text(self.build_sym_var.get()),
        )

    def _run_simulator(self) -> None:
        try:
            argv = build_run_argv(self._collect_run_config())
        except ValueError as exc:
            messagebox.showerror("无法运行", str(exc))
            return
        self._start_command("运行模拟器", "mcs51", cli_main, argv)

    def _run_list_symbols(self) -> None:
        try:
            config = self._collect_run_config()
            config = replace(config, list_symbols=True)
            argv = build_run_argv(config)
        except ValueError as exc:
            messagebox.showerror("无法列出符号", str(exc))
            return
        self._start_command("列出符号", "mcs51", cli_main, argv)

    def _build_source(self) -> None:
        try:
            argv = build_build_argv(self._collect_build_config())
        except ValueError as exc:
            messagebox.showerror("无法编译", str(exc))
            return
        self._start_command("编译 ASM", "mcs51.build", build_main, argv)

    def _copy_build_source_to_run(self) -> None:
        source = _clean_text(self.build_source_var.get())
        if source:
            self.image_var.set(source)
            self.format_var.set("asm")
            self.status_var.set("已把 Source ASM 复制到运行面板。")

    def _start_command(
        self,
        title: str,
        module_name: str,
        entrypoint: Callable[[list[str] | None], int],
        argv: list[str],
    ) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showwarning("命令仍在执行", "请等待当前任务完成。")
            return

        self._set_busy(True, f"{title}中...")

        def worker() -> None:
            result = _invoke_entrypoint(title, module_name, entrypoint, argv)
            self.result_queue.put(result)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _poll_result_queue(self) -> None:
        try:
            while True:
                result = self.result_queue.get_nowait()
                self._append_output(result)
                status = "完成" if result.exit_code == 0 else f"失败(退出码 {result.exit_code})"
                self._set_busy(False, f"{result.title}{status}")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_result_queue)

    def _append_output(self, result: CommandResult) -> None:
        self.output_text.configure(state="normal")
        divider = "\u2500" * 72
        status_tag = "success" if result.exit_code == 0 else "error"
        exit_label = "OK" if result.exit_code == 0 else f"EXIT {result.exit_code}"

        self.output_text.insert("end", f"{divider}\n", "divider")
        self.output_text.insert("end", f"  [{result.title}] ", "header")
        self.output_text.insert("end", f"{exit_label}\n", status_tag)
        self.output_text.insert("end", f"  {result.command}\n", "divider")
        self.output_text.insert("end", f"{divider}\n", "divider")
        self.output_text.insert("end", f"{result.output}\n", "output")
        self.output_text.see("end")
        self.output_text.configure(state="disabled")

    def _clear_output(self) -> None:
        self.output_text.configure(state="normal")
        self.output_text.delete("1.0", "end")
        self.output_text.configure(state="disabled")
        self.status_var.set("输出已清空")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_var.set(status)
        state = "disabled" if busy else "normal"
        for button in self.action_buttons:
            button.configure(state=state)

        if busy:
            fg = "#e08000"
        elif "失败" in status:
            fg = "#d32f2f"
        elif "完成" in status:
            fg = "#2e7d32"
        else:
            fg = ""
        try:
            self.status_label.configure(foreground=fg)
        except (AttributeError, tk.TclError):
            pass

    def _make_button(self, parent: ttk.Widget, text: str, command: Callable[[], None]) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        self.action_buttons.append(button)
        return button

    def _add_path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        browse_command: Callable[[], None],
        save_dialog: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        self._make_button(parent, "浏览", browse_command).grid(row=row, column=2, sticky="e", padx=(0, 4), pady=6)
        if save_dialog:
            ttk.Label(parent, text="").grid(row=row, column=3, padx=2)

    def _add_entry_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        width: int = 30,
        values: tuple[str, ...] | None = None,
        help_text: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        if values:
            widget = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=width - 2)
        else:
            widget = ttk.Entry(parent, textvariable=variable, width=width)
        widget.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        if help_text:
            ttk.Label(parent, text=help_text).grid(row=row, column=2, sticky="w", padx=(0, 8), pady=6)

    def _open_file(self, **kwargs: object) -> str | None:
        selected = filedialog.askopenfilename(**kwargs)
        return selected or None

    def _save_file(self, **kwargs: object) -> str | None:
        selected = filedialog.asksaveasfilename(**kwargs)
        return selected or None

    def _browse_project(self) -> None:
        path = self._open_file(
            title="选择 project.json",
            filetypes=[("Project JSON", "*.json"), ("All Files", "*.*")],
        )
        if path:
            self.project_var.set(path)

    def _browse_image(self) -> None:
        path = self._open_file(
            title="选择镜像文件",
            filetypes=[("8051 Images", "*.asm *.hex *.bin"), ("All Files", "*.*")],
        )
        if path:
            self.image_var.set(path)
            lowered = path.lower()
            if lowered.endswith(".asm"):
                self.format_var.set("asm")
            elif lowered.endswith(".hex"):
                self.format_var.set("hex")
            elif lowered.endswith(".bin"):
                self.format_var.set("bin")
            else:
                self.format_var.set("auto")

    def _browse_runtime(self) -> None:
        path = self._open_file(
            title="选择 runtime JSON",
            filetypes=[("Runtime JSON", "*.json"), ("All Files", "*.*")],
        )
        if path:
            self.runtime_var.set(path)

    def _browse_symbols(self) -> None:
        path = self._open_file(
            title="选择符号文件",
            filetypes=[("Symbol JSON", "*.json"), ("All Files", "*.*")],
        )
        if path:
            self.symbols_var.set(path)

    def _browse_trace_output(self) -> None:
        path = self._save_file(
            title="选择 trace 输出文件",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All Files", "*.*")],
        )
        if path:
            self.trace_file_var.set(path)

    def _browse_build_source(self) -> None:
        path = self._open_file(
            title="选择 ASM 源文件",
            filetypes=[("ASM", "*.asm"), ("All Files", "*.*")],
        )
        if path:
            self.build_source_var.set(path)

    def _browse_build_bin(self) -> None:
        path = self._save_file(
            title="选择 BIN 输出",
            defaultextension=".bin",
            filetypes=[("BIN", "*.bin"), ("All Files", "*.*")],
        )
        if path:
            self.build_bin_var.set(path)

    def _browse_build_hex(self) -> None:
        path = self._save_file(
            title="选择 HEX 输出",
            defaultextension=".hex",
            filetypes=[("HEX", "*.hex"), ("All Files", "*.*")],
        )
        if path:
            self.build_hex_var.set(path)

    def _browse_build_sym(self) -> None:
        path = self._save_file(
            title="选择 SYM 输出",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All Files", "*.*")],
        )
        if path:
            self.build_sym_var.set(path)

    def _load_hello_example(self) -> None:
        self._reset_run_form()
        self.image_var.set("examples/hello_uart.asm")
        self.format_var.set("asm")
        self.max_instructions_var.set("256")
        self.status_var.set("已加载 hello_uart 示例。")

    def _load_echo_example(self) -> None:
        self._reset_run_form()
        self.image_var.set("examples/echo_timer_demo.asm")
        self.format_var.set("asm")
        self.serial_input_var.set("AB\r")
        self.max_instructions_var.set("512")
        self.watchpoints_var.set("30H")
        self.inspect_var.set("TMOD,TR0")
        self.trace_interrupts_var.set(True)
        self.status_var.set("已加载 echo_timer_demo 示例。")

    def _reset_run_form(self) -> None:
        self.project_var.set("")
        self.image_var.set("")
        self.format_var.set("auto")
        self.origin_var.set("")
        self.entry_var.set("")
        self.max_instructions_var.set("512")
        self.serial_input_var.set("")
        self.serial_input_hex_var.set("")
        self.runtime_var.set("")
        self.symbols_var.set("")
        self.breakpoints_var.set("")
        self.watchpoints_var.set("")
        self.inspect_var.set("")
        self.step_var.set("")
        self.dump_direct_var.set("")
        self.dump_xram_var.set("")
        self.trace_file_var.set("")
        self.trace_limit_var.set("64")
        self.watch_log_var.set(True)
        self.list_symbols_var.set(False)
        self.trace_ports_var.set(False)
        self.trace_interrupts_var.set(True)
        self.dump_iram_var.set(False)
        self.dump_sfr_var.set(False)
        self.tight_loop_detect_var.set(True)


def main() -> int:
    if tk is None:  # pragma: no cover - depends on local Python install
        print("当前 Python 环境缺少 tkinter，无法启动图形界面。")
        return 1

    classic = "--classic" in sys.argv

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - depends on display availability
        print(f"无法启动图形界面: {exc}")
        return 1

    EmulatorGui(root, classic=classic)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
