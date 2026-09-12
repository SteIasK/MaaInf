"""
In Falsus 剧情跳过运行器

用法:
    python runner.py             # 运行「剧情跳过」主任务
    python runner.py 连接测试    # 运行指定入口节点
    python runner.py --dry       # 只连接窗口并截图检查(不执行任何点击), 用于排查黑屏

紧急停止:
    F10 全局热键(游戏内也生效), 或在运行本脚本的控制台按 Ctrl+C

依赖:
    pip install -r requirements.txt

点击说明:
    MaaFramework 自带的 Seize 输入在本游戏上点击无效(识别/截图正常),
    因此 pipeline 中所有点击动作都走自定义动作 win_click:
    用经过验证的 raw SendInput 原语(SetCursorPos + 按下保持 80ms + 抬起)直接执行。
"""

import ctypes
import json
import os
import re
import sys
import time
from pathlib import Path

# 打包为 exe 后, 把工作目录锚定到 exe 所在位置 (assets/debug 跟随分发)
if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).parent)

try:
    import numpy as np
    from maa.custom_action import CustomAction
    from maa.custom_recognition import CustomRecognition
    from maa.toolkit import Toolkit
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.controller import Win32Controller
    from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
except ImportError:
    import traceback
    traceback.print_exc()
    print("未安装 MaaFw, 请先执行: pip install -r requirements.txt")
    sys.exit(1)

WINDOW_TITLE_RE = re.compile(r"in.?falsus", re.IGNORECASE)
DEFAULT_ENTRY = "剧情_主循环"

# ---------------- raw SendInput 点击原语 (click_test.py 已验证可用) ----------------

ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def raw_click(x, y):
    """注入 MOVE|ABSOLUTE 事件同步游戏内指针位置, 再按下保持 80ms + 抬起。
    只 SetCursorPos 不注入 MOVE 时, Unity Input System 的内部指针位置不会更新,
    点击会被射线检测到旧位置上(表现为"点了没反应")。"""
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
    screen_h = user32.GetSystemMetrics(1)   # SM_CYSCREEN

    def send(mi):
        inp = _INPUT(type=0)  # INPUT_MOUSE
        inp.mi = mi
        ret = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if ret != 1:
            print(f"[raw_click] SendInput 失败! flags={mi.dwFlags} "
                  f"ret={ret} GetLastError={ctypes.GetLastError()}")

    # 1) 显式注入绝对移动事件, 让游戏内指针位置同步到目标点
    move = _MOUSEINPUT(dx=int(x * 65535 / (screen_w - 1)),
                       dy=int(y * 65535 / (screen_h - 1)),
                       mouseData=0, time=0, dwExtraInfo=0)
    move.dwFlags = 0x0001 | 0x8000  # MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    send(move)
    time.sleep(0.03)
    # 2) 轻微抖动: 生成不同位置的移动事件, 强制 UI 刷新悬停/命中状态
    #    (否则偶发"光标已就位但 UI 无反应", 手动抖一下鼠标才识别)
    jitter = _MOUSEINPUT(dx=int((x + 3) * 65535 / (screen_w - 1)),
                         dy=int((y + 2) * 65535 / (screen_h - 1)),
                         mouseData=0, time=0, dwExtraInfo=0)
    jitter.dwFlags = 0x0001 | 0x8000
    send(jitter)
    time.sleep(0.03)
    back = _MOUSEINPUT(dx=int(x * 65535 / (screen_w - 1)),
                       dy=int(y * 65535 / (screen_h - 1)),
                       mouseData=0, time=0, dwExtraInfo=0)
    back.dwFlags = 0x0001 | 0x8000
    send(back)
    time.sleep(0.03)

    # 2) 按下保持 80ms (跨帧, Unity 必定能感知)
    down = _MOUSEINPUT(dx=0, dy=0, mouseData=0, time=0, dwExtraInfo=0)
    down.dwFlags = 0x0002  # MOUSEEVENTF_LEFTDOWN
    send(down)
    time.sleep(0.08)

    # 3) 抬起
    up = _MOUSEINPUT(dx=0, dy=0, mouseData=0, time=0, dwExtraInfo=0)
    up.dwFlags = 0x0004  # MOUSEEVENTF_LEFTUP
    send(up)


# ---------------- 自定义识别/动作: pipeline 的点击与状态判定 ----------------

class FfAvailable(CustomRecognition):
    """快进键状态判定 (仅对话界面且"可用"时命中):
    亮像素计数区分 可用/置灰/快进中 + 对话锚点 NCC 守卫防止非对话界面误命中。"""

    BRIGHT_MIN = 160    # 灰度阈值
    BRIGHT_COUNT = 40   # 下限: 少于该数视为置灰
    BRIGHT_MAX = 400    # 上限: 多于该数视为"正在快进"(白色暂停键)
    ANCHOR_XY = (1215, 30)   # 对话锚点区域左上角 (内容区坐标)
    ANCHOR_WH = (40, 35)
    ANCHOR_NCC = 0.68    # 锚点模板匹配阈值 (对话 0.79+, 非对话 <=0.47)

    def __init__(self, x_off=0, y_off=0):
        super().__init__()
        self.x_off = x_off
        self.y_off = y_off
        self._anchor_tmpl = None   # 对话锚点模板 (灰度浮点), 惰性加载

    def _anchor_ok(self, image):
        """对话锚点守卫: 在右上角区域做 NCC 匹配日志图标。
        亮像素计数会被卡牌页封面缩略图等亮色 UI 骗过, NCC 只认图标本身。"""
        if self._anchor_tmpl is None:
            from PIL import Image
            p = Path("assets/resource/image/story/dialog_anchor.png")
            self._anchor_tmpl = np.array(Image.open(p).convert("L"), dtype=float)
        ax = self.x_off + self.ANCHOR_XY[0]
        ay = self.y_off + self.ANCHOR_XY[1]
        aw, ah = self.ANCHOR_WH
        region = image[ay:ay + ah + 12, ax:ax + aw + 12].mean(axis=2)
        return max_ncc(region, self._anchor_tmpl) >= self.ANCHOR_NCC

    def analyze(self, context, argv):
        # 对话锚点守卫: 不在对话界面直接不命中
        if not self._anchor_ok(argv.image):
            return None  # 非对话界面, 静默跳过(不打印, 每轮都会经过)

        roi = argv.roi
        x, y, w, h = int(roi.x), int(roi.y), int(roi.w), int(roi.h)
        crop = argv.image[y:y + h, x:x + w]
        gray = crop.mean(axis=2)
        bright = int((gray > self.BRIGHT_MIN).sum())
        if self.BRIGHT_COUNT <= bright <= self.BRIGHT_MAX:
            return CustomRecognition.AnalyzeResult(
                box=[x, y, w, h], detail={"bright": bright})
        state = "置灰" if bright < self.BRIGHT_COUNT else "正在快进"
        print(f"[ff_active_reco] 快进键{state} (亮像素 {bright}), 不点击")
        return None


class WinClick(CustomAction):
    """把 pipeline 的 target 区域(1280x800 控制器坐标系)换算成屏幕坐标后 raw 点击"""

    def __init__(self, hwnd=None):
        super().__init__()
        self._hwnd = hwnd
        self._streak = 0   # 兜底点击连续计数, 用于发现未知界面
        self._client = None  # 首次点击时的客户区尺寸, 用于检测窗口被改变

    def run(self, context, argv):
        try:
            try:
                param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
            except (ValueError, TypeError):
                param = {}
            if not isinstance(param, dict):
                param = {}  # 框架未传参时会传 "null", json.loads 得到 None

            hwnd = self._hwnd or self._resolve_hwnd()
            if not hwnd:
                print("[win_click] 找不到游戏窗口, 跳过点击")
                return False

            user32 = ctypes.windll.user32
            # 前台守卫: 焦点被其他窗口(通知/聊天窗)抢走时, 点击会落到别人身上。
            # 先自动夺回前台并验证, 夺不回就跳过本次点击。
            for _ in range(3):
                if user32.GetForegroundWindow() == hwnd:
                    break
                print(f"[win_click] {argv.node_name}: 检测到前台丢失, 尝试夺回...")
                user32.keybd_event(0x12, 0, 0, 0)        # ALT down, 解除前台切换限制
                user32.SetForegroundWindow(hwnd)
                user32.keybd_event(0x12, 0, 0x0002, 0)   # ALT up
                time.sleep(0.2)
            if user32.GetForegroundWindow() != hwnd:
                print(f"[win_click] {argv.node_name}: 无法夺回前台(可能有全屏弹窗), 跳过点击")
                return False

            rect, origin = _RECT(), _POINT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            user32.ClientToScreen(hwnd, ctypes.byref(origin))
            client_w, client_h = rect.right - rect.left, rect.bottom - rect.top
            if not client_w or not client_h:
                print(f"[win_click] {argv.node_name}: 窗口客户区尺寸为 0 (被最小化?), 放弃点击")
                return False
            if self._client and (client_w, client_h) != self._client:
                print(f"[win_click] 警告: 窗口尺寸从 {self._client} 变为 "
                      f"{client_w}x{client_h}, 模板几何失配, 识别可能失效! "
                      f"请恢复窗口大小后重启脚本")
                return False
            if not self._client:
                self._client = (client_w, client_h)

            if param.get("screen_center"):
                # 点击画面正中 (用于"点任意处推进"类节点)
                cx, cy = origin.x + client_w / 2, origin.y + client_h / 2
            else:
                # 点击 target 区域中心; box 在当前截图的坐标系里,
                # 按实际截图尺寸换算 (兼容不同短边设置与画面比例)
                try:
                    img = context.tasker.controller.cached_image
                    img_w, img_h = img.shape[1], img.shape[0]
                except Exception:
                    img_w, img_h = 1280, 800
                b = argv.box
                scale_x, scale_y = client_w / img_w, client_h / img_h
                cx = origin.x + (b.x + b.w / 2) * scale_x
                cy = origin.y + (b.y + b.h / 2) * scale_y

            print(f"[win_click] {argv.node_name} -> 屏幕({cx:.0f},{cy:.0f}) box=({b_label(argv.box)})")
            raw_click(cx, cy)

            if param.get("then_center"):
                # 快进节点专用: 点完快进后再点一次画面中心。
                # 快进置灰时该点击是无害空操作, 但保证不可跳过段也有中心点击推进,
                # 不再依赖"快进可用/置灰"的精确判定。
                ccx, ccy = origin.x + client_w / 2, origin.y + client_h / 2
                print(f"[win_click] {argv.node_name} -> 屏幕({ccx:.0f},{ccy:.0f}) (中心推进)")
                raw_click(ccx, ccy)

            self._report_unknown(context, argv)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[win_click] {getattr(argv, 'node_name', '?')} 异常: {e!r}")
            return False

    def _resolve_hwnd(self):
        """agent 子进程模式下没有预绑定窗口, 按需查找一次"""
        from runner import find_game_window
        w = find_game_window()
        self._hwnd = w.hwnd if w else None
        return self._hwnd

    def _report_unknown(self, context, argv):
        """未知界面哨兵: 兜底点击连续 15 次说明画面不被任何节点认识, 截图存证。"""
        if argv.node_name != "剧情_过渡点击":
            self._streak = 0
            return
        self._streak += 1
        if self._streak % 15:
            return
        try:
            img = context.tasker.controller.post_screencap().wait().get()
            from PIL import Image
            d = Path("./debug/unknown")
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"unknown_{time.strftime('%m%d_%H%M%S')}.png"
            Image.fromarray(img[:, :, ::-1]).save(f)
            print(f"[win_click] 连续 {self._streak} 次兜底点击: 疑似未知界面, 已存证 {f}")
        except Exception as e:  # noqa: BLE001
            print(f"[win_click] 存证失败: {e!r}")


def b_label(box):
    try:
        return f"{box.x},{box.y},{box.w},{box.h}"
    except Exception:
        return repr(box)


# ---------------- 窗口与任务 ----------------

def enable_dpi_awareness():
    """声明 Per-Monitor DPI 感知。
    否则 Python 进程拿到的是 150% 缩放后的虚拟坐标(÷1.5),
    而 SetCursorPos/SendInput 吃物理坐标 -> 所有点击都偏到真实位置的 2/3 处。"""
    user32 = ctypes.windll.user32
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "PerMonitorV2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # fallback: PER_MONITOR_AWARE
        return "PerMonitor"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()  # 最后兜底: SYSTEM_AWARE
        return "System"
    except Exception:
        return "失败(进程已初始化?)"


def strip_jsonc(s):
    """去掉 JSONC 的 // 注释 (正确处理字符串内的 // 与引号)"""
    out, i, in_str = [], 0, False
    while i < len(s):
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(s):
                out.append(s[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1
        else:
            if c == '"':
                in_str = True; out.append(c); i += 1
            elif c == "/" and i + 1 < len(s) and s[i + 1] == "/":
                while i < len(s) and s[i] != "\n":
                    i += 1
            else:
                out.append(c); i += 1
    return "".join(out)


def load_roi_override(path, dx, dy):
    """读取 pipeline JSON, 为所有带 roi 的节点生成 y/x 平移后的覆盖参数。
    用于分辨率自适应: 内容区在不同客户区里的截图偏移不同。"""
    with open(path, encoding="utf-8") as f:
        nodes = json.loads(strip_jsonc(f.read()))
    override = {}
    for name, node in nodes.items():
        if "roi" in node:
            x, y, w, h = node["roi"]
            x, y = x + dx, y + dy
            # 钳制到截图范围内 (负数 roi 在协议里有特殊语义, 不能用)
            ny = max(0, y)
            h -= ny - y
            nx = max(0, x)
            w -= nx - x
            if w > 0 and h > 0:
                override[name] = {"roi": [nx, ny, w, h]}
    return override


def compute_adaptation(client_w, client_h):
    """分辨率自适应: 游戏内容恒为 16:9 (基准 1280x720)。
    按客户区尺寸计算截图短边与内容区在截图里的偏移, 使得:
    任意分辨率/比例/窗口大小下, 内容区在截图中都恰好是 1280x720。
    返回 (short_side, x_off, y_off)。"""
    if client_w < client_h:  # 竖屏异常兜底
        client_w, client_h = client_h, client_w
    k = 1280.0 / min(client_w, client_h * 16 / 9)
    img_w, img_h = round(client_w * k), round(client_h * k)
    content_w = round(min(client_w, client_h * 16 / 9) * k)
    content_h = round(min(client_h, client_w * 9 / 16) * k)
    short_side = min(img_w, img_h)
    x_off = (img_w - content_w) // 2
    y_off = (img_h - content_h) // 2
    return short_side, x_off, y_off


def max_ncc(region, tmpl):
    """归一化互相关最大值 (region/tmpl 均为二维灰度浮点数组)"""
    th, tw = tmpl.shape
    ih, iw = region.shape
    if ih < th or iw < tw:
        return 0.0
    t = tmpl - tmpl.mean()
    tn = (t * t).sum() ** 0.5
    if tn == 0:
        return 0.0
    best = 0.0
    for yy in range(ih - th + 1):
        for xx in range(iw - tw + 1):
            win = region[yy:yy + th, xx:xx + tw]
            w0 = win - win.mean()
            d = (w0 * w0).sum() ** 0.5
            if d:
                ncc = abs((w0 * t).sum() / (d * tn))
                if ncc > best:
                    best = ncc
    return best


def probe_window(hwnd):
    """打印窗口真实几何信息, 验证坐标系"""
    user32 = ctypes.windll.user32
    rect, origin = _RECT(), _POINT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    wrect = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wrect))
    try:
        dpi = user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
    except Exception:
        dpi = "?"
    client_w, client_h = rect.right - rect.left, rect.bottom - rect.top
    print(f"窗口几何: 客户区 {client_w}x{client_h} @屏幕({origin.x},{origin.y}), "
          f"外框({wrect.left},{wrect.top})-({wrect.right},{wrect.bottom}), DPI={dpi}")
    return client_w, client_h, origin.x, origin.y


def fit_window_client(hwnd, cw=1280, ch=800):
    """把窗口客户区调整为指定尺寸(含标题栏/边框补偿), 返回是否成功。
    窗口模式标准化: 客户区 1280x800 (16:10) 时画面几何与 16:10 全屏完全一致,
    一套模板通吃。显示器分辨率不重要, 只要装得下窗口。"""
    user32 = ctypes.windll.user32
    GWL_STYLE, GWL_EXSTYLE = -16, -20
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    bdr, exbdr = _RECT(), _RECT()
    user32.AdjustWindowRectEx(ctypes.byref(bdr), style, False, exstyle)
    user32.AdjustWindowRectEx(ctypes.byref(exbdr), style, False,
                              exstyle | 0x180)  # WS_EX_CLIENTEDGE 补偿
    w = cw + (bdr.right - bdr.left) + (exbdr.left + exbdr.right)
    h = ch + (bdr.bottom - bdr.top) + (exbdr.top + exbdr.bottom)
    # 居中放置
    mx = user32.GetSystemMetrics(0)
    my = user32.GetSystemMetrics(1)
    x, y = max(0, (mx - w) // 2), max(0, (my - h) // 2)
    SWP_NOZORDER = 0x0004
    ok = user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER)
    rect, origin = _RECT(), _POINT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    real_w, real_h = rect.right - rect.left, rect.bottom - rect.top
    print(f"窗口标准化: 目标客户区 {cw}x{ch}, 实际 {real_w}x{real_h} @({origin.x},{origin.y}) "
          f"({'OK' if (real_w, real_h) == (cw, ch) else '偏差, 建议手动调整窗口大小'})")
    return bool(ok)


def find_game_window():
    wins = Toolkit.find_desktop_windows()
    hits = [w for w in wins
            if w.class_name == "UnityWndClass" and WINDOW_TITLE_RE.search(w.window_name or "")]
    if hits:
        return hits[0]
    unity = [w for w in wins if w.class_name == "UnityWndClass"]
    print("未按标题找到 In Falsus 窗口。当前所有 Unity 窗口:")
    for w in unity:
        print(f"  - 标题={w.window_name!r}")
    if len(unity) == 1:
        print("-> 只有这一个 Unity 窗口, 自动采用。")
        return unity[0]
    print("-> 请先启动游戏再运行本脚本。")
    return None


def install_stop_hotkey(tasker: Tasker):
    try:
        import keyboard
        keyboard.add_hotkey("f10", lambda: (print("\n[F10] 正在停止任务..."),
                                            tasker.post_stop()))
        print("紧急停止热键已就绪: F10 (全局生效)")
    except Exception as e:  # noqa: BLE001
        print(f"全局热键不可用({e}), 请在本控制台按 Ctrl+C 停止")


def preload_maa_binaries():
    """预加载 maa/bin 下全部 DLL (LOAD_WITH_ALTERED_SEARCH_PATH)。

    打包 (PyInstaller) 后, MaaToolkit.dll 等的依赖不再走开发环境的系统搜索顺序,
    会初始化失败 (WinError 1114)。预加载让每个 DLL 的依赖从它自己的目录解析,
    且框架之后按名字加载时直接命中已加载的模块。开发环境下此操作无害。"""
    import maa
    bin_dir = Path(maa.__file__).parent / "bin"
    if not bin_dir.is_dir():
        return
    k32 = ctypes.windll.kernel32
    for dll in sorted(bin_dir.glob("*.dll")):
        if not k32.LoadLibraryExW(str(dll), None, 0x8):
            print(f"[preload] {dll.name} 加载失败 (GetLastError={ctypes.GetLastError()})")


def build_stack(fit1280=False):
    """绑定游戏窗口并构建完整识别栈 (控制器/资源/自定义识别/任务器)。
    返回 (tasker, override); 失败返回 None。控制台与 GUI 共用。"""
    print(f"DPI 感知模式: {enable_dpi_awareness()}")
    preload_maa_binaries()
    Toolkit.init_option("./debug")  # 日志与调试截图输出到 ./debug

    w = find_game_window()
    if w is None:
        return None
    print(f"已绑定窗口: {w.window_name!r}")

    if fit1280:
        # 窗口模式标准化: 客户区 1280x800, 画面几何与 16:10 全屏一致
        fit_window_client(w.hwnd)
    cw, ch, ox, oy = probe_window(w.hwnd)

    short_side, x_off, y_off = compute_adaptation(cw, ch)
    print(f"分辨率自适应: 截图 {round(cw*short_side/ch)}x{short_side}, "
          f"内容区 1280x720 偏移=({x_off},{y_off})")

    ctrl = Win32Controller(
        w.hwnd,
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.Seize,   # 仅作为占位; 实际点击全部走 win_click
        keyboard_method=MaaWin32InputMethodEnum.Seize,
    )
    ctrl.post_connection().wait()
    ctrl.set_screenshot_target_short_side(short_side)

    res = Resource()
    res.register_custom_action("win_click", WinClick(w.hwnd))
    res.register_custom_recognition("ff_active_reco", FfAvailable(x_off, y_off))
    res.post_bundle("assets/resource").wait()
    if not res.loaded:
        print("资源加载失败, 请检查 assets/resource 目录与 pipeline JSON 语法")
        return None
    print("资源加载完成 (点击动作 win_click 已注册)")

    tasker = Tasker()
    if not tasker.bind(res, ctrl) or not tasker.inited:
        print("Tasker 初始化失败")
        return None

    # roi 覆盖量 = 当前环境偏移 - 标定环境偏移(模板/roi 在 16:10 全屏 800 短边下标定,
    # 黑边偏移已含在标定值里), 不能直接叠加当前偏移, 否则 16:10 下会双重偏移
    _, cal_x, cal_y = compute_adaptation(2560, 1600)
    override = load_roi_override("assets/resource/pipeline/story.json",
                                 x_off - cal_x, y_off - cal_y)
    return tasker, override


def main():
    args = [a for a in sys.argv[1:] if a != "--dry" and a != "--fit1280"]
    dry = "--dry" in sys.argv
    fit1280 = "--fit1280" in sys.argv
    entry = args[0] if args else DEFAULT_ENTRY

    stack = build_stack(fit1280)
    if stack is None:
        sys.exit(1)
    tasker, override = stack

    ctrl = tasker.controller
    if dry:
        img = ctrl.post_screencap().wait().get()
        h, wd = img.shape[:2]
        brightness = img.mean()
        print(f"截图 {wd}x{h}, 平均亮度 {brightness:.1f} "
              f"({'正常' if brightness > 10 else '接近全黑, 请更换 interface.json 里的 screencap 方式'})")
        return

    install_stop_hotkey(tasker)
    print(f"3 秒后启动任务「{entry}」, 请切换到游戏窗口...")
    time.sleep(3)

    try:
        job = tasker.post_task(entry, override)
        job.wait()
        status = getattr(job.status, "name", job.status)
        print(f"任务结束, status = {status}")
    except KeyboardInterrupt:
        print("\n[Ctrl+C] 正在停止任务...")
        tasker.post_stop().wait()
        print("已停止")


if __name__ == "__main__":
    main()
