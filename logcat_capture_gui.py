import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

try:
    import customtkinter as ctk
except ImportError:
    print("缺少 customtkinter。请先运行: pip install customtkinter")
    raise


APP_TITLE = "Logcat Capture"
APP_ICON_NAME = "sage.ico"
LOGCAT_FORMAT = "threadtime"
LOG_PREVIEW_FLUSH_INTERVAL_MS = 120
LOG_PREVIEW_MAX_LINES = 1200
REFRESH_SHAKE_INTERVAL_MS = 4500


@dataclass
class DeviceInfo:
    serial: str
    state: str
    description: str

    @property
    def display_name(self) -> str:
        if self.description:
            return f"{self.serial}  ({self.state})  {self.description}"
        return f"{self.serial}  ({self.state})"


def bundled_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS, *parts)
    return bundled_base_dir().joinpath(*parts)


def candidate_adb_paths() -> list[Path]:
    names = ["adb.exe"] if os.name == "nt" else ["adb"]
    bases: list[Path] = []

    env_vars = ["ANDROID_HOME", "ANDROID_SDK_ROOT"]
    for env_var in env_vars:
        value = os.environ.get(env_var)
        if value:
            bases.append(Path(value) / "platform-tools")

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        if local_app_data:
            bases.append(Path(local_app_data) / "Android" / "Sdk" / "platform-tools")
        if program_files:
            bases.append(Path(program_files) / "Android" / "android-sdk" / "platform-tools")
        if program_files_x86:
            bases.append(Path(program_files_x86) / "Android" / "android-sdk" / "platform-tools")
    else:
        home = Path.home()
        bases.extend(
            [
                home / "Android" / "Sdk" / "platform-tools",
                home / "Library" / "Android" / "sdk" / "platform-tools",
                Path("/opt/android-sdk/platform-tools"),
                Path("/usr/local/android-sdk/platform-tools"),
            ]
        )

    bases.extend([bundled_base_dir(), bundled_base_dir() / "platform-tools"])

    candidates: list[Path] = []
    seen: set[Path] = set()
    for base in bases:
        for name in names:
            path = (base / name).expanduser()
            if path not in seen:
                candidates.append(path)
                seen.add(path)
    return candidates


def find_adb() -> str | None:
    from_path = shutil.which("adb")
    if from_path:
        return str(Path(from_path).resolve())

    for candidate in candidate_adb_paths():
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def run_adb(adb_path: str, args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [adb_path, *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def parse_adb_devices(output: str) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*") or "daemon" in line.lower():
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial, state = parts[0], parts[1]
        description = " ".join(parts[2:])
        devices.append(DeviceInfo(serial=serial, state=state, description=description))
    return devices


class LogcatCaptureApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.adb_path: str | None = None
        self.devices: list[DeviceInfo] = []
        self.selected_serial: str | None = None
        self.log_lines: list[str] = []
        self.reader_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.logcat_process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.is_starting = False
        self.is_recording = False
        self.is_closing = False
        self.started_at: datetime | None = None
        self.missing_adb_dialog_shown = False
        self.preview_buffer: list[str] = []
        self.preview_flush_scheduled = False
        self.shaking_buttons: set[int] = set()
        self.refresh_shake_after_id: str | None = None
        self.is_refreshing_devices = False

        self._configure_window()
        self._build_layout()
        self.after(100, self.bootstrap)
        self.after(100, self.drain_reader_queue)

    def _configure_window(self) -> None:
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("green")
        self.title(APP_TITLE)
        self.geometry("980x720")
        self.minsize(860, 620)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.apply_window_icon()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

    def apply_window_icon(self) -> None:
        icon_path = resource_path("assets", APP_ICON_NAME)
        if icon_path.is_file():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

    def _build_layout(self) -> None:
        self.header = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray95", "gray12"))
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            self.header,
            text="Android Logcat 日志录制助手",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 4))

        self.adb_path_label = ctk.CTkLabel(
            self.header,
            text="ADB 路径：正在检测...",
            font=ctk.CTkFont(size=13),
            anchor="w",
            wraplength=900,
        )
        self.adb_path_label.grid(row=1, column=0, sticky="ew", padx=24, pady=(2, 0))

        self.adb_status_label = ctk.CTkLabel(
            self.header,
            text="",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        )
        self.adb_status_label.grid(row=2, column=0, sticky="ew", padx=24, pady=(2, 18))

        self.controls = ctk.CTkFrame(self, corner_radius=8)
        self.controls.grid(row=1, column=0, sticky="ew", padx=24, pady=(20, 12))
        self.controls.grid_columnconfigure(3, weight=1)

        self.refresh_button = ctk.CTkButton(self.controls, text="刷新设备", command=self.refresh_devices)
        self.refresh_button.grid(row=0, column=0, padx=(16, 8), pady=16)

        self.clear_buffer_var = ctk.BooleanVar(value=True)
        self.clear_buffer_checkbox = ctk.CTkCheckBox(
            self.controls,
            text="只录新日志（开始前清空旧缓冲）",
            variable=self.clear_buffer_var,
        )
        self.clear_buffer_checkbox.grid(row=0, column=1, padx=8, pady=16)

        self.record_button = ctk.CTkButton(
            self.controls,
            text="开始记录",
            command=self.toggle_recording,
            width=140,
            height=40,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.record_button.grid(row=0, column=4, padx=(8, 16), pady=16)
        self.record_button_default_fg = self.record_button.cget("fg_color")
        self.record_button_default_hover = self.record_button.cget("hover_color")

        self.status_bar = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray92", "gray16"))
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.status_bar.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="准备就绪。请选择设备后开始记录。",
            anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=16, pady=10)
        self.counter_label = ctk.CTkLabel(self.status_bar, text="0 行", font=ctk.CTkFont(size=13, weight="bold"))
        self.counter_label.grid(row=0, column=1, padx=16, pady=10)

        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 24))
        self.main.grid_columnconfigure(0, weight=1, uniform="main")
        self.main.grid_columnconfigure(1, weight=2, uniform="main")
        self.main.grid_rowconfigure(1, weight=1)

        self.device_header = ctk.CTkLabel(
            self.main,
            text="可选择的设备列表",
            anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.device_header.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 8))

        self.preview_header = ctk.CTkLabel(
            self.main,
            text="实时预览",
            anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.preview_header.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 8))

        self.device_list = ctk.CTkScrollableFrame(self.main, corner_radius=8)
        self.device_list.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.device_list.grid_columnconfigure(0, weight=1)

        self.preview_text = ctk.CTkTextbox(self.main, corner_radius=8, wrap="none")
        self.preview_text.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        self.preview_text.insert("end", "日志会在开始记录后显示在这里。\n")
        self.preview_text.configure(state="disabled")

    def bootstrap(self) -> None:
        self.detect_adb(show_dialog=True)
        self.refresh_devices()

    def detect_adb(self, show_dialog: bool) -> None:
        self.adb_path = find_adb()
        if self.adb_path:
            self.adb_path_label.configure(text=f"ADB 路径：{self.adb_path}")
            self.adb_status_label.configure(
                text="已找到 ADB，可以刷新设备并开始抓取 logcat。",
                text_color=("#17883b", "#4ade80"),
            )
            return

        self.adb_path_label.configure(text="ADB 路径：未找到")
        self.adb_status_label.configure(
            text="未找到 ADB。请安装 Android SDK Platform Tools，或把 adb 加入 PATH。",
            text_color=("#c62828", "#ff6b6b"),
        )
        if show_dialog and not self.missing_adb_dialog_shown:
            self.missing_adb_dialog_shown = True
            messagebox.showwarning(
                "未找到 ADB",
                "程序没有在 PATH 或常见 Android SDK 目录中找到 adb。\n\n"
                "请安装 Android SDK Platform Tools，或者把 adb.exe 所在目录加入系统 PATH 后重新打开程序。",
            )

    def refresh_devices(self) -> None:
        self.cancel_refresh_button_hint()
        self.is_refreshing_devices = True
        self.detect_adb(show_dialog=False)
        self.clear_device_list()
        self.devices = []
        self.selected_serial = None
        self.record_button.configure(state="disabled")

        if not self.adb_path:
            self.is_refreshing_devices = False
            self.set_status("找不到 ADB，暂时不能读取设备列表。")
            self.add_empty_device_hint("没有 ADB，无法检测设备。")
            return

        self.set_status("正在刷新设备列表...")
        threading.Thread(target=self._load_devices_worker, daemon=True).start()

    def _load_devices_worker(self) -> None:
        try:
            result = run_adb(self.adb_path or "adb", ["devices", "-l"])
            devices = parse_adb_devices(result.stdout)
            if result.stderr.strip():
                self.reader_queue.put(("status", result.stderr.strip()))
            self.reader_queue.put(("devices", devices))
        except subprocess.TimeoutExpired:
            self.reader_queue.put(("status", "刷新设备超时，请确认 adb server 正常。"))
            self.reader_queue.put(("devices", []))
        except OSError as exc:
            self.reader_queue.put(("status", f"启动 ADB 失败：{exc}"))
            self.reader_queue.put(("devices", []))

    def render_devices(self) -> None:
        self.is_refreshing_devices = False
        self.clear_device_list()
        if not self.devices:
            self.add_empty_device_hint("没有发现可用设备。请连接手机、授权 USB 调试后点击“刷新设备”。")
            self.set_status("没有发现可用设备。可以连接手机并授权 USB 调试后点击“刷新设备”。")
            if self.adb_path and not self.is_starting and not self.is_recording:
                self.schedule_refresh_button_hint(initial_ms=900)
            return

        self.cancel_refresh_button_hint()

        for index, device in enumerate(self.devices):
            selectable = device.state == "device"
            color = ("#17883b", "#4ade80") if selectable else ("#b26a00", "#fbbf24")
            frame = ctk.CTkFrame(self.device_list, corner_radius=8)
            frame.grid(row=index, column=0, sticky="ew", padx=8, pady=(8, 0))
            frame.grid_columnconfigure(0, weight=1)

            title = ctk.CTkLabel(
                frame,
                text=device.serial,
                anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"),
            )
            title.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))

            subtitle_text = f"状态：{device.state}"
            if device.description:
                subtitle_text += f"   {device.description}"
            subtitle = ctk.CTkLabel(frame, text=subtitle_text, anchor="w", text_color=color, wraplength=280)
            subtitle.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 10))

            button = ctk.CTkButton(
                frame,
                text="选择",
                width=78,
                command=lambda d=device: self.select_device(d),
                state="normal" if selectable and not self.is_starting and not self.is_recording else "disabled",
            )
            button.grid(row=0, column=1, rowspan=2, padx=12, pady=12)

        usable_count = sum(1 for device in self.devices if device.state == "device")
        self.set_status(f"发现 {len(self.devices)} 台设备，其中 {usable_count} 台可抓取 logcat。")

    def clear_device_list(self) -> None:
        for widget in self.device_list.winfo_children():
            widget.destroy()

    def add_empty_device_hint(self, text: str) -> None:
        hint = ctk.CTkLabel(self.device_list, text=text, wraplength=280, justify="left")
        hint.grid(row=0, column=0, padx=16, pady=18, sticky="ew")

    def shake_button(self, button: ctk.CTkButton, base_padx: tuple[int, int]) -> None:
        key = id(button)
        if key in self.shaking_buttons:
            return

        self.shaking_buttons.add(key)
        offsets = [0, -7, 7, -6, 6, -4, 4, 0]

        def step(index: int) -> None:
            if not button.winfo_exists():
                self.shaking_buttons.discard(key)
                return

            if index >= len(offsets):
                button.grid_configure(padx=base_padx)
                self.shaking_buttons.discard(key)
                return

            offset = offsets[index]
            left = max(0, base_padx[0] + offset)
            right = max(0, base_padx[1] - offset)
            button.grid_configure(padx=(left, right))
            self.after(45, lambda: step(index + 1))

        step(0)

    def schedule_refresh_button_hint(self, initial_ms: int = REFRESH_SHAKE_INTERVAL_MS) -> None:
        if self.refresh_shake_after_id is not None:
            return
        self.refresh_shake_after_id = self.after(initial_ms, self.refresh_button_hint_tick)

    def cancel_refresh_button_hint(self) -> None:
        if self.refresh_shake_after_id is None:
            return
        try:
            self.after_cancel(self.refresh_shake_after_id)
        except Exception:
            pass
        self.refresh_shake_after_id = None

    def refresh_button_hint_tick(self) -> None:
        self.refresh_shake_after_id = None
        should_hint = (
            bool(self.adb_path)
            and not self.devices
            and not self.is_refreshing_devices
            and not self.is_starting
            and not self.is_recording
            and not self.is_closing
        )
        if not should_hint:
            return

        self.shake_button(self.refresh_button, (16, 8))
        self.set_status("没有发现可用设备。连接手机并授权 USB 调试后，点击“刷新设备”。")
        self.schedule_refresh_button_hint()

    def select_device(self, device: DeviceInfo) -> None:
        if device.state != "device":
            messagebox.showinfo("设备不可用", "只有状态为 device 的设备才能抓取 logcat。")
            return
        self.cancel_refresh_button_hint()
        self.selected_serial = device.serial
        self.record_button.configure(state="normal")
        self.set_status(f"已选择设备：{device.serial}。现在可以点击“开始记录”。")
        self.shake_button(self.record_button, (8, 16))

    def toggle_recording(self) -> None:
        if self.is_starting:
            return
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        self.cancel_refresh_button_hint()
        if not self.adb_path:
            self.detect_adb(show_dialog=True)
            return

        if not self.selected_serial:
            messagebox.showinfo("请选择设备", "请先在设备列表里选择一台状态为 device 的设备。")
            return

        serial = self.selected_serial
        adb_path = self.adb_path
        clear_buffer = self.clear_buffer_var.get()
        self.log_lines = []
        self.preview_buffer = []
        self.started_at = datetime.now()
        self.is_starting = True
        self.is_recording = False
        self.set_status(f"正在启动 {serial} 的 logcat...")
        self.counter_label.configure(text="0 行")
        self.record_button.configure(text="启动中...", state="disabled")
        self.refresh_button.configure(state="disabled")
        self.clear_buffer_checkbox.configure(state="disabled")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", f"开始记录：{self.started_at:%Y-%m-%d %H:%M:%S}\n")
        self.preview_text.configure(state="disabled")
        self.render_devices()

        threading.Thread(
            target=self._start_recording_worker,
            args=(adb_path, serial, clear_buffer),
            daemon=True,
        ).start()

    def _start_recording_worker(self, adb_path: str, serial: str, clear_buffer: bool) -> None:
        clear_warning: str | None = None
        if clear_buffer:
            try:
                run_adb(adb_path, ["-s", serial, "logcat", "-c"], timeout=5)
            except Exception as exc:
                clear_warning = f"未能清空 logcat 缓冲，但仍可继续记录。\n\n{exc}"

        if self.is_closing or not self.is_starting:
            return

        cmd = [adb_path, "-s", serial, "logcat", "-v", LOGCAT_FORMAT]
        try:
            process = subprocess.Popen(
                cmd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            self.reader_queue.put(("recording_start_failed", f"无法启动 logcat：{exc}"))
            return

        if self.is_closing or not self.is_starting:
            if process.poll() is None:
                process.terminate()
            return

        self.reader_queue.put(("recording_started", (process, clear_warning)))

    def finish_start_recording(self, process: subprocess.Popen[str], clear_warning: str | None) -> None:
        if not self.is_starting:
            if process.poll() is None:
                process.terminate()
            return

        self.logcat_process = process
        self.is_starting = False
        self.is_recording = True
        self.set_status(f"正在记录 {self.selected_serial} 的 logcat...")
        self.record_button.configure(
            text="停止记录",
            state="normal",
            fg_color=("#c62828", "#dc2626"),
            hover_color=("#a61b1b", "#b91c1c"),
        )
        self.render_devices()

        self.reader_thread = threading.Thread(target=self._read_logcat_worker, daemon=True)
        self.reader_thread.start()

        if clear_warning:
            messagebox.showwarning("清空缓冲失败", clear_warning)

    def _read_logcat_worker(self) -> None:
        process = self.logcat_process
        if not process or not process.stdout:
            return

        for line in process.stdout:
            self.reader_queue.put(("log", line))

        code = process.wait()
        self.reader_queue.put(("process_exit", f"logcat 已退出，退出码：{code}"))

    def stop_recording(self) -> None:
        process = self.logcat_process
        if process and process.poll() is None:
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    process.send_signal(signal.SIGINT)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass

        self.finish_recording_ui()
        self.save_log_file()

    def finish_recording_ui(self) -> None:
        self.is_starting = False
        self.is_recording = False
        self.logcat_process = None
        self.record_button.configure(
            text="开始记录",
            state="normal" if self.selected_serial else "disabled",
            fg_color=self.record_button_default_fg,
            hover_color=self.record_button_default_hover,
        )
        self.refresh_button.configure(state="normal")
        self.clear_buffer_checkbox.configure(state="normal")
        self.render_devices()

    def save_log_file(self) -> None:
        if not self.log_lines:
            messagebox.showinfo("没有日志", "这次没有记录到 logcat 内容，因此没有生成文件。")
            self.set_status("记录已停止，但没有捕获到日志内容。")
            return

        serial = (self.selected_serial or "device").replace(":", "_").replace("/", "_").replace("\\", "_")
        timestamp = (self.started_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
        default_name = f"logcat_{serial}_{timestamp}.txt"
        path = filedialog.asksaveasfilename(
            title="保存 logcat 日志",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("Log files", "*.log"), ("All files", "*.*")],
        )
        if not path:
            self.set_status("记录已停止。用户取消了保存。")
            return

        Path(path).write_text("".join(self.log_lines), encoding="utf-8", errors="replace")
        self.set_status(f"日志已保存：{path}")
        messagebox.showinfo("保存完成", f"日志已保存到：\n{path}")

    def drain_reader_queue(self) -> None:
        processed = 0
        try:
            while processed < 500:
                processed += 1
                kind, payload = self.reader_queue.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "devices":
                    self.devices = payload
                    self.render_devices()
                elif kind == "status":
                    self.set_status(str(payload))
                elif kind == "recording_started":
                    process, clear_warning = payload
                    self.finish_start_recording(process, clear_warning)
                elif kind == "recording_start_failed":
                    self.finish_recording_ui()
                    self.set_status(str(payload))
                    messagebox.showerror("启动失败", str(payload))
                elif kind == "process_exit":
                    if self.is_recording:
                        self.finish_recording_ui()
                        self.set_status(str(payload))
                        if self.log_lines:
                            self.after(100, self.save_log_file)
        except queue.Empty:
            pass
        delay = 1 if processed >= 500 else 80
        self.after(delay, self.drain_reader_queue)

    def append_log(self, line: str) -> None:
        self.log_lines.append(line)
        self.preview_buffer.append(line)
        if len(self.log_lines) % 25 == 0:
            self.counter_label.configure(text=f"{len(self.log_lines)} 行")

        if not self.preview_flush_scheduled:
            self.preview_flush_scheduled = True
            self.after(LOG_PREVIEW_FLUSH_INTERVAL_MS, self.flush_log_preview)

    def flush_log_preview(self) -> None:
        self.preview_flush_scheduled = False
        if not self.preview_buffer:
            return

        chunk = "".join(self.preview_buffer)
        self.preview_buffer = []

        self.preview_text.configure(state="normal")
        self.preview_text.insert("end", chunk)
        self.preview_text.see("end")

        if int(float(self.preview_text.index("end-1c").split(".")[0])) > LOG_PREVIEW_MAX_LINES:
            self.preview_text.delete("1.0", "250.0")
        self.preview_text.configure(state="disabled")
        self.counter_label.configure(text=f"{len(self.log_lines)} 行")

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def on_close(self) -> None:
        self.is_closing = True
        self.cancel_refresh_button_hint()
        if self.is_starting:
            should_close = messagebox.askyesno(
                "正在启动",
                "当前正在启动 logcat。关闭窗口会取消本次启动。是否继续？",
            )
            if not should_close:
                self.is_closing = False
                return
            self.is_starting = False

        if self.is_recording:
            should_stop = messagebox.askyesno(
                "正在记录",
                "当前仍在记录 logcat。关闭窗口会停止记录，并询问是否保存已捕获的内容。是否继续？",
            )
            if not should_stop:
                self.is_closing = False
                return
            self.stop_recording()
        self.destroy()


if __name__ == "__main__":
    app = LogcatCaptureApp()
    app.mainloop()
