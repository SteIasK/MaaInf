"""
MaaInf 图形壳: tkinter 界面 + 子进程运行器。

- 界面本身不加载 maa (规避 PyInstaller 与 maa 原生库的运行库冲突)
- 启动 = 拉起 python\\python.exe -u runner.py 子进程, 实时显示其日志
- 停止 = 终止子进程; 全局热键 F9 启动 / F10 停止 (游戏内可按)
"""

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext

# 界面进程启用 per-monitor DPI 感知, 高分屏下 tkinter 文字不发虚 (失败则退回默认)
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# onefile 打包后 __file__ 在临时解压目录 (_MEIxxxxx), 必须以 exe 自身位置为基准
BASE = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)
PY = BASE / "python" / "python.exe"

_q: "queue.Queue[tuple]" = queue.Queue()
_proc: subprocess.Popen | None = None
_running = False
_fit = False  # fit_var 的普通变量镜像: 热键回调在子线程, 不能跨线程读 tk 变量
_lock = threading.Lock()

# ---- 平面风格配色 ----
BG = "#f6f7f9"          # 窗口底色
TEXT = "#1f2328"        # 主文字
SUBTLE = "#8b949e"      # 次要文字
GREEN = "#1f9d4d"       # 启动键
GREEN_HOVER = "#27b259"
RED = "#d43d3d"         # 停止键
RED_HOVER = "#e04f4f"
BTN_OFF_BG = "#e6e9ed"  # 禁用键底色
BTN_OFF_FG = "#a6adb5"
LOG_BG = "#151a21"      # 日志面板 (深色终端风)
LOG_FG = "#c9d1d9"
WHITE = "#ffffff"

UI_FONT = ("Segoe UI", 10)
SMALL_FONT = ("Segoe UI", 9)
TITLE_FONT = ("Segoe UI", 14, "bold")
BTN_FONT = ("Segoe UI", 10, "bold")
MONO_FONT = ("Consolas", 9)


def is_running():
    with _lock:
        p, r = _proc, _running
    return bool(p and r and p.poll() is None)


def reader(proc):
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _q.put(("log", line))
    except Exception:  # noqa: BLE001
        pass
    _q.put(("finished", None))


def start():
    global _proc, _running
    with _lock:
        if _running:
            return
        if not PY.exists():
            _q.put(("log", f"缺少解释器: {PY}"))
            _q.put(("log", "MaaInf.exe 必须与发布包内其它文件同目录使用 (需要 python\\, assets\\, runner.py)。"
                           "请解压完整压缩包后运行包内的 MaaInf.exe, 不要单独拷贝 exe。"))
            return
        cmd = [str(PY), "-u", "runner.py"]
        if _fit:
            cmd.append("--fit1280")
        flags = 0x08000000  # CREATE_NO_WINDOW
        # 子进程 stdout 在管道下默认走系统本地编码 (中文 Windows = GBK),
        # 而 GUI 读取端按 UTF-8 解码; 强制子进程 UTF-8, 两端一致才不乱码
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        _proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace",
                                 cwd=str(BASE), creationflags=flags, env=env)
        _running = True
    _q.put(("started", None))
    _q.put(("log", "已启动运行器 (加载中... 约 3 秒后生效)"))
    threading.Thread(target=reader, args=(_proc,), daemon=True).start()


def stop():
    with _lock:
        p, r = _proc, _running
    if p is None or not r:
        return
    _q.put(("log", "正在停止运行器..."))
    try:
        p.terminate()  # TerminateProcess; 我们的点击均为完整的按下-抬起对, 强杀安全
    except OSError:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("MaaInf — In Falsus 剧情跳过")
        root.geometry("800x540")
        root.minsize(620, 420)
        root.configure(bg=BG)

        # 头部: 标题 + 副标题
        head = tk.Frame(root, bg=BG)
        head.pack(fill=tk.X, padx=24, pady=(18, 4))
        tk.Label(head, text="In Falsus 剧情跳过助手", font=TITLE_FONT,
                 fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(head, text="剧情段自动推进 · 音游段静默, 交还手动操作",
                 font=SMALL_FONT, fg=SUBTLE, bg=BG).pack(anchor="w")

        # 控制行: 启动 / 停止 / 窗口标准化
        bar = tk.Frame(root, bg=BG)
        bar.pack(fill=tk.X, padx=24, pady=(14, 10))
        self.btn_start = self._button(bar, "启动  F9", GREEN, GREEN_HOVER, start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_stop = self._button(bar, "停止  F10", RED, RED_HOVER, stop)
        self.btn_stop.pack(side=tk.LEFT, padx=(10, 20))
        self._set_enabled(self.btn_stop, False)

        global fit_var
        fit_var = tk.BooleanVar(value=False)  # 必须在 root 创建之后
        tk.Checkbutton(bar, text="窗口标准化 1280×800", variable=fit_var,
                       command=self._sync_fit, font=SMALL_FONT,
                       fg=TEXT, bg=BG, activebackground=BG,
                       activeforeground=TEXT, highlightthickness=0,
                       cursor="hand2").pack(side=tk.LEFT, pady=(4, 0))

        # 状态行: 指示点 + 文案
        status = tk.Frame(root, bg=BG)
        status.pack(fill=tk.X, padx=24, pady=(0, 10))
        self.dot = tk.Label(status, text="●", font=UI_FONT, fg=SUBTLE, bg=BG)
        self.dot.pack(side=tk.LEFT)
        self.status = tk.Label(status, text="待机 — 打开游戏后点启动",
                               font=SMALL_FONT, fg=SUBTLE, bg=BG)
        self.status.pack(side=tk.LEFT, padx=(7, 0))

        # 日志面板: 深色终端风, 占满剩余空间
        logwrap = tk.Frame(root, bg=LOG_BG)
        logwrap.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 20))
        self.logbox = scrolledtext.ScrolledText(
            logwrap, font=MONO_FONT, bg=LOG_BG, fg=LOG_FG,
            insertbackground=LOG_FG, relief=tk.FLAT, bd=0,
            highlightthickness=0, state=tk.DISABLED)
        self.logbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        try:
            import keyboard
            keyboard.add_hotkey("f9", start)
            keyboard.add_hotkey("f10", stop)
            self._log("全局热键已就绪: F9 = 启动, F10 = 停止")
        except Exception as e:  # noqa: BLE001
            self._log(f"全局热键不可用 ({e}), 请使用界面按钮")

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(120, self._drain)

    @staticmethod
    def _button(parent, text, normal, hover, cmd):
        b = tk.Button(parent, text=text, font=BTN_FONT, fg=WHITE, bg=normal,
                      activebackground=hover, activeforeground=WHITE,
                      relief=tk.FLAT, bd=0, padx=24, pady=9, cursor="hand2",
                      command=cmd, disabledforeground=BTN_OFF_FG)
        b._normal, b._hover = normal, hover
        b.bind("<Enter>", lambda e: b["state"] == tk.NORMAL and b.configure(bg=b._hover))
        b.bind("<Leave>", lambda e: b.configure(
            bg=b._normal if b["state"] == tk.NORMAL else BTN_OFF_BG))
        return b

    @staticmethod
    def _set_enabled(btn, enabled: bool):
        if enabled:
            btn.configure(state=tk.NORMAL, bg=btn._normal, fg=WHITE)
        else:
            btn.configure(state=tk.DISABLED, bg=BTN_OFF_BG, fg=BTN_OFF_FG)

    def _sync_fit(self):
        global _fit
        _fit = bool(fit_var.get())

    def _log(self, msg):
        _q.put(("log", str(msg)))

    def _drain(self):
        global _running
        try:
            while True:
                kind, msg = _q.get_nowait()
                if kind == "log":
                    self.logbox.configure(state=tk.NORMAL)
                    self.logbox.insert(tk.END, msg + "\n")
                    self.logbox.see(tk.END)
                    self.logbox.configure(state=tk.DISABLED)
                elif kind == "started":
                    self._set_enabled(self.btn_start, False)
                    self._set_enabled(self.btn_stop, True)
                    self.dot.configure(fg=GREEN)
                    self.status.configure(text="任务运行中 — F10 停止", fg=TEXT)
                elif kind == "finished":
                    _running = False  # 复位, 否则停止后再点启动会被 start() 拒绝
                    self._set_enabled(self.btn_start, True)
                    self._set_enabled(self.btn_stop, False)
                    self.dot.configure(fg=SUBTLE)
                    self.status.configure(text="已结束", fg=SUBTLE)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _on_close(self):
        stop()
        self.root.destroy()


root = tk.Tk()  # 先建根窗口, 之后才能创建 tk 变量
app = App(root)


def main():
    app._log("MaaInf 图形界面已启动")
    app._log(f"运行器解释器: {PY} " + ("(已找到)" if PY.exists() else "(缺失!)"))
    app._log("先启动游戏 (全屏或窗口), 再点启动; 全局热键 F9/F10 随时可按")
    root.mainloop()


if __name__ == "__main__":
    main()
