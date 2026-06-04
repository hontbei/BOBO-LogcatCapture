# BOBO Logcat Capture

一个用于抓取 Android `adb logcat` 的桌面 GUI 小工具。程序会自动检测 ADB、列出可用设备、选择设备后开始/停止记录，并在停止后让你保存完整日志文件。

## 功能

- 启动时自动检测 `adb` 路径。
- 找不到 `adb` 时弹窗提示安装 Android SDK Platform Tools。
- 显示当前可抓取 logcat 的设备列表。
- 选择设备后提示点击“开始记录”。
- 支持“只录新日志（开始前清空旧缓冲）”。
- 录制时日志保存在内存中，并实时预览。
- 停止录制后弹出保存窗口，把完整日志保存到本地。
- 支持打包成 Windows exe。

## 运行方式

先安装 Python 3，然后在项目目录中运行：

```powershell
pip install -r requirements.txt
python logcat_capture_gui.py
```

也可以直接双击：

```text
run_logcat_capture.bat
```

这个启动脚本会自动检查 Python 和 `customtkinter`，缺少依赖时会提示安装。

## ADB 准备

如果程序提示找不到 ADB，请安装 Android SDK Platform Tools，并把 `adb.exe` 所在目录加入系统 `PATH`。

手机端需要：

- 开启开发者选项。
- 开启 USB 调试。
- 连接电脑后在手机上允许 USB 调试授权。
- 设备状态需要是 `device`，如果是 `unauthorized` 或 `offline`，暂时不能抓取日志。

## “只录新日志”的作用

`adb logcat` 默认会先输出设备缓冲区里已经存在的旧日志，然后继续输出新日志。

如果勾选“只录新日志（开始前清空旧缓冲）”，程序会在开始录制前执行：

```text
adb logcat -c
```

这样保存出来的日志更接近“从点击开始记录这一刻开始”。如果你需要保留点击之前的历史日志，可以取消勾选。

## 打包 exe

双击或运行：

```text
build_exe.bat
```

脚本会自动安装缺失的打包依赖，并使用 PyInstaller 输出：

```text
build\BOBOLogcatCapture\BOBOLogcatCapture.exe
```

`build/` 是打包产物目录，已经加入 `.gitignore`，不会上传到 GitHub。
