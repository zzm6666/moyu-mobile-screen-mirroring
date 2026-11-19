# -*- coding: utf-8 -*-
"""连接设备向导：逻辑层 + 自绘 UI（纯 ctypes Win32，无第三方依赖）。

逻辑层（可离线单测，全部 adb 调用走可注入的 runner）：
  - friendly_error / pair_only / connect_only：报错一律翻译成通俗中文，
    原始英文只进日志；
  - list_devices_detailed / discover_mdns / merge_devices：设备三源合并
    （在线 / mDNS 发现 / 魔芋记住的历史设备）；
  - device_display_name：营销名（一加 13）→ 型号（PJZ110）→ IP 兜底，
    绝不把 mDNS 机器串号当名字显示；
  - reconnect_known：历史设备一键重连（旧端口 → mDNS 新端口）。

UI 层（show_wizard）：
  两个 Tab「无线连接 / USB 连接」；设备列表每行带「连接」按钮；
  首次配对只有 IP:端口 + 配对码 + 一个「连接设备」大按钮，
  点击后配对→找连接端口→连接全自动，用户无感。
  窗口自绘（GDI 圆角/配色/微软雅黑），输入框仍是原生 EDIT 保证输入法可用。
"""
import ctypes
import ctypes.wintypes as wt
import inspect
import json
import os
import random
import re
import subprocess
import threading
import time

try:                      # 自动发现手机配对端口（纯标准库 mDNS 浏览器）
    import mdns_browse
except Exception:         # 环境里没有这个模块时，退化为「只能手填 IP:端口」
    mdns_browse = None

try:                      # 二维码配对：只需要把 QR 载荷算成矩阵，画图还是自己用 GDI
    import segno
except Exception:         # 没有这个模块时，面板里只保留「配对码」一种方式
    segno = None

CREATE_NO_WINDOW = 0x08000000
IP_PORT_RE = re.compile(r'^[\d.]+:\d+$')

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_SETFONT = 0x0030
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_PRINTCLIENT = 0x0318
WM_SETICON = 0x0080
WM_SETTEXT = 0x000C
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_CTLCOLORSTATIC = 0x0138
WM_CTLCOLOREDIT = 0x0133
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_MOUSELEAVE = 0x02A3
WM_MOUSEWHEEL = 0x020A
WM_SETCURSOR = 0x0020
WM_KEYDOWN = 0x0100
WM_APP_JOB = 0x8000 + 7          # 工作线程做完通知窗口
WM_APP_TICK = 0x8000 + 8         # 忙碌动画（省略号）节拍
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_ESCAPE = 0x1B
BN_CLICKED = 0
EN_CHANGE = 0x0300
EM_SETMARGINS = 0x00D3       # 单行 EDIT 左右留白

WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_CLIPCHILDREN = 0x02000000   # 自绘不盖掉 EDIT 子控件
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
ES_AUTOHSCROLL = 0x0080
ES_NUMBER = 0x2000
SW_SHOW = 5
SW_HIDE = 0
TME_LEAVE = 0x0002
HTCLIENT = 1

ID_IP, ID_CODE, ID_GO, ID_STATUS, ID_PORT = range(1001, 1006)
IDOK = 1                       # IsDialogMessage 回车→默认按钮的 WM_COMMAND id
ID_TAB_WIFI, ID_TAB_USB = 1101, 1102
ID_REFRESH = 1103
ID_MANUAL = 1104                 # 配对面板收起时的「手动配对」链接
ID_PAIR_CLOSE = 1105             # 配对面板右上角「收起」
ID_MODE_CODE, ID_MODE_QR = 1106, 1107   # 配对面板里「配对码 / 二维码」切换
ID_QR_REFRESH = 1108             # 二维码面板里「刷新」（换一张码）
ID_ROW_BASE = 1200               # 设备行「连接」按钮：ID_ROW_BASE + 行号

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    """ctypes.wintypes 里没有 WNDCLASSW，自己写一份（字段顺序必须与 Win32 一致）。"""
    _fields_ = [
        ('style', wt.UINT), ('lpfnWndProc', _WNDPROC), ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON),
        ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH),
        ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [('hdc', wt.HDC), ('fErase', wt.BOOL), ('rcPaint', wt.RECT),
                ('fRestore', wt.BOOL), ('fIncUpdate', wt.BOOL),
                ('rgbReserved', ctypes.c_byte * 32)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('dwFlags', wt.DWORD),
                ('hwndTrack', wt.HWND), ('dwHoverTime', wt.DWORD)]


_PROC_REFS = []
_WINDOWS = {}          # hwnd -> ctx
_EDIT_OLD = {}         # edit hwnd -> 原 wndproc（子类化 placeholder 用）

user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = ctypes.c_void_p
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterClassW.restype = wt.ATOM
user32.SendMessageW.restype = ctypes.c_void_p
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wt.HWND]
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.DestroyIcon.argtypes = [wt.HICON]
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.GetFocus.restype = wt.HWND
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetDlgCtrlID.argtypes = [wt.HWND]
user32.GetDlgCtrlID.restype = ctypes.c_int
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.IsDialogMessageW.argtypes = [wt.HWND, ctypes.POINTER(wt.MSG)]
user32.IsDialogMessageW.restype = wt.BOOL
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_void_p, wt.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_void_p]
user32.SetCapture.argtypes = [wt.HWND]
user32.ReleaseCapture.restype = wt.BOOL
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.SetCursor.argtypes = [wt.HANDLE]
user32.SetCursor.restype = wt.HANDLE
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
gdi32.CreateFontW.restype = wt.HGDIOBJ
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HGDIOBJ
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
gdi32.CreatePen.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.SetBkColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.TextOutW.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.LPCWSTR, ctypes.c_int]
gdi32.Rectangle.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.MoveToEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.LineTo.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetTextExtentPoint32W.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(wt.SIZE)]
# 离屏双缓冲：WM_PAINT 先画到内存位图再 BitBlt 一次上屏，避免直画屏幕 DC 闪烁
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.DeleteDC.restype = wt.BOOL


# ---------------------------------------------------------------------------
# 逻辑层
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout=25):
    """跑一个命令，返回 (returncode, stdout+stderr 文本)。"""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=timeout)
        out = (r.stdout or b'') + (r.stderr or b'')
        return r.returncode, out.decode('utf-8', errors='ignore')
    except Exception as exc:                     # 找不到 adb / 超时
        return -1, str(exc)


def normalize_ip_port(text):
    """把用户输入的「IP:端口」规范化；不合格返回空串。"""
    t = (text or '').strip()
    return t if IP_PORT_RE.match(t) else ''


def join_ip_port(ip, port):
    """IP、端口分两个输入框填写，冒号由程序拼，用户不用输「:」。

    兼容在 IP 框直接粘贴整串「IP:端口」；两边都填时端口以独立框为准。
    不合格返回空串。
    """
    ip = (ip or '').strip()
    port = (port or '').strip()
    if ':' in ip:
        head, _, tail = ip.partition(':')
        ip = head.strip()
        port = port or tail.strip()
    return normalize_ip_port('%s:%s' % (ip, port))


def classify_device(serial):
    """带冒号或 mDNS 服务名的是无线（adb connect 出来的），否则就是 USB 串号。"""
    s = serial or ''
    return 'wifi' if (':' in s or '_tcp' in s) else 'usb'


def valid_code(code):
    return re.match(r'^\d{6}$', (code or '').strip()) is not None


# adb 报错 → 通俗中文 + 下一步建议。顺序有意义：先匹配更具体的。
_ERROR_RULES = (
    ('successfully paired', None),   # 成功标记，调用方先判
    ('already connected', None),
    ('10061', '手机拒绝了连接：多半是端口不对。请填手机「无线调试」主页面上的'
              '「IP 地址和端口」（不是配对弹窗里的端口），再点连接。'),
    ('10060', '连接超时：电脑找不到这台手机。请确认手机和电脑连的是同一个 Wi-Fi，'
              '且手机「无线调试」开关还开着。'),
    ('10064', '手机暂时不可达：请确认手机没锁屏断网、和电脑在同一个 Wi-Fi，然后重试。'),
    ('protocol fault', '配对握手失败：配对码或配对端口可能已过期。请在手机上重新打开'
                       '「使用配对码配对设备」，用新弹出的端口和配对码再试一次'
                       '（那个端口和投屏用的端口不是同一个）。'),
    ('failed to authenticate', '手机没有授权这台电脑：请看手机屏幕，勾选「一律允许」并点允许。'),
    ('unauthorized', '手机还没有授权这台电脑：请解锁手机，在弹出的「允许 USB 调试」里点允许。'),
    ('no such device', '这台设备当前不在线：请确认手机无线调试还开着，然后点「刷新」。'),
    ('device offline', '设备掉线了：请重开一次手机上的「无线调试」开关，然后点「刷新」。'),
    ('cannot connect', '连不上手机：请确认 IP 和端口填的是手机屏幕上显示的最新值'
                       '（无线调试每次重开端口都会变）。'),
    ('connection refused', '手机拒绝了连接：端口可能已经变了。请在手机上重新确认'
                           '「IP 地址和端口」后再试。'),
    ('timed out', '等待手机响应超时：请确认手机和电脑在同一个 Wi-Fi，稍后重试。'),
    ('timeout', '等待手机响应超时：请确认手机和电脑在同一个 Wi-Fi，稍后重试。'),
    ('no devices', '没有找到任何设备：请先用下面的「首次配对」连一次，或改用 USB 数据线。'),
    ('not found', '找不到 adb 组件：请重新安装魔芋，或联系开发者。'),
    ('winerror 2', '找不到 adb 组件：请重新安装魔芋，或联系开发者。'),
    ('filenotfounderror', '找不到 adb 组件：请重新安装魔芋，或联系开发者。'),
)


def friendly_error(raw, action='连接'):
    """把 adb 的原始英文输出翻译成通俗中文；raw 只该进日志，不该给用户看。"""
    low = (raw or '').lower()
    for key, msg in _ERROR_RULES:
        if msg and key in low:
            return msg
    if not low.strip():
        return ('%s没有成功：手机没有响应。请确认手机「无线调试」还开着，'
                '端口和配对码用的是手机上最新显示的值。' % action)
    return ('%s没有成功：请确认手机和电脑在同一个 Wi-Fi、手机「无线调试」已开启，'
            '然后重试。若反复失败，请在手机上关闭再打开「无线调试」，用新端口重试。' % action)


def parse_mdns_services(text, ip=''):
    """从 `adb mdns services` 里挑出该 IP 的无线调试连接端口。

    输出形如：`adb-bfd7e543-MoDo9u _adb-tls-connect._tcp. 192.168.137.198:41001`
    """
    ip = (ip or '').strip()
    for line in (text or '').splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if '_adb-tls-connect._tcp' not in parts[1]:
            continue
        addr = parts[-1]
        if ip and not addr.startswith(ip + ':'):
            continue
        if IP_PORT_RE.match(addr):
            return addr
    return ''


def parse_mdns_all(text):
    """`adb mdns services` → [{'name','serial','addr'}]，只要无线调试连接服务。"""
    out = []
    for line in (text or '').splitlines():
        parts = line.split()
        if len(parts) < 3 or '_adb-tls-connect._tcp' not in parts[1]:
            continue
        addr = parts[-1]
        if not IP_PORT_RE.match(addr):
            continue
        out.append({'name': parts[0], 'serial': parts[0] + '._adb-tls-connect._tcp',
                    'addr': addr})
    return out


def _ip_of(ip_port):
    return (ip_port or '').split(':')[0]


def pair_only(adb, ip_port, code, runner=run_cmd):
    """只做 adb pair；返回 (是否成功, 通俗提示)。"""
    if not normalize_ip_port(ip_port):
        return False, '请填写「IP:配对端口」，例如 192.168.1.5:43847'
    if not valid_code(code):
        return False, '配对码是手机屏幕上的 6 位数字'
    rc, out = runner([adb, 'pair', ip_port, (code or '').strip()])
    if 'Successfully paired' not in (out or ''):
        return False, friendly_error(out, '配对')
    return True, ''


def connect_only(adb, ip_port, runner=run_cmd):
    """只做 adb connect；返回 (是否成功, 通俗提示, 串号)。

    手头的 ip:port 可能已经过期（每次开关无线调试端口都会变），所以失败时
    重新问一次 mDNS 拿最新端口再试；这台手机已经以别的串号在线时直接算成功。
    """
    if not normalize_ip_port(ip_port):
        return False, '请填写「IP:端口」，例如 192.168.1.5:41001', ''
    rc, out = runner([adb, 'connect', ip_port])
    low = (out or '').lower()
    if 'connected to' in low or 'already connected' in low:
        return True, '连接成功', resolve_serial(adb, ip_port, runner)
    # 端口过期：按 IP 找 mDNS 最新端口再连一次
    stale = connect_only_fresh(adb, ip_port, runner)
    if stale:
        return stale
    return False, friendly_error(out, '连接'), ''


def connect_only_fresh(adb, ip_port, runner=run_cmd):
    """用 mDNS 里的最新端口重试同一台手机；成功返回 (True, msg, serial)。"""
    ip = _ip_of(ip_port or '')
    if not ip:
        return None
    out = runner([adb, 'mdns', 'services'])[1]
    for d in parse_mdns_all(out):
        if _ip_of(d['addr']) != ip:
            continue
        if d['addr'] == ip_port:
            continue
        rc, res = runner([adb, 'connect', d['addr']])
        low = (res or '').lower()
        if 'connected to' in low or 'already connected' in low:
            return True, '连接成功', resolve_serial(adb, d['addr'], runner)
    return None


def resolve_serial(adb, wanted, runner=run_cmd):
    """连接成功后问 adb 到底用哪个串号认这台手机。

    adb 常把 ip:port 自动升级成 mDNS 串号（`adb-xxx._adb-tls-connect._tcp`），
    两边不一致时投屏会「启动即退出」（scrcpy 找不到设备），所以统一成在线的那个。
    """
    if not wanted:
        return wanted
    devs = list_online(adb, runner)
    if wanted in devs:
        return wanted
    ip = _ip_of(wanted)
    if not ip:
        return wanted
    for s in devs:
        if _ip_of(s) == ip:
            return s
    out = runner([adb, 'mdns', 'services'])[1]
    for d in parse_mdns_all(out):
        if _ip_of(d['addr']) == ip and d['serial'] in devs:
            return d['serial']
    return wanted


def list_online(adb, runner=run_cmd):
    """当前在线设备（状态 device 才算）。"""
    rc, out = runner([adb, 'devices'])
    devs = []
    for line in (out or '').splitlines()[1:]:
        parts = line.split()
        # 容忍 `adb devices -l` 那种后面还带 product:/model: 的输出
        if len(parts) >= 2 and parts[1] == 'device':
            devs.append(parts[0])
    return devs


def parse_devices_l(text):
    """`adb devices -l` → [{'serial','model','product','transport_id'}]，只要在线的。"""
    devs = []
    for line in (text or '').splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != 'device':
            continue
        d = {'serial': parts[0], 'model': '', 'product': '', 'transport_id': ''}
        for kv in parts[2:]:
            if ':' in kv:
                k, v = kv.split(':', 1)
                if k in ('model', 'product', 'transport_id'):
                    d[k] = v
        devs.append(d)
    return devs


def list_devices_detailed(adb, runner=run_cmd):
    rc, out = runner([adb, 'devices', '-l'])
    return parse_devices_l(out)


_MARKET_PROPS = ('ro.product.marketname', 'ro.vendor.oplus.market.name',
                 'ro.product.oppo.marketname', 'ro.config.marketing_name',
                 'ro.product.vendor.marketname')


def device_display_name(adb, serial, model='', runner=run_cmd, timeout=6):
    """给人看的设备名：营销名（一加 13）→ 型号（PJZ110）→ IP/串号兜底。

    绝不用 mDNS 机器串号（adb-xxxx._adb-tls-connect._tcp）当名字。
    """
    if serial:
        cmd = [adb, '-s', serial, 'shell']
        cmd += ['getprop %s;' % p for p in _MARKET_PROPS]
        cmd += ['getprop ro.product.model']
        try:
            rc, out = runner(cmd, timeout=timeout)
            vals = [v.strip() for v in (out or '').splitlines() if v.strip()]
            for v in vals:
                # 营销名/型号都不该是机器串号或空 prop
                if v and '_tcp' not in v and not v.startswith('adb-'):
                    return v
        except Exception:
            pass
    if model:
        return model
    if IP_PORT_RE.match(serial or ''):
        return '无线设备 ' + _ip_of(serial)
    if '_tcp' in (serial or ''):
        return '无线设备'   # mDNS 机器串号绝不原样显示（那截 MoDo9u 用户看不懂）
    return serial or '未知设备'


def _is_junk_name(name, serial=''):
    """旧版本会把串号/ip:port 当名字存下来，这种名字对用户毫无意义。"""
    name = (name or '').strip()
    if not name:
        return True
    if serial and name == serial:
        return True
    return name.startswith('adb-') or bool(IP_PORT_RE.match(name))


PAIR_HINT_MANUAL = ('把设备「无线调试」页上的 IP 和端口填进上面两个框，'
                    '再填设备「使用配对码配对设备」页上的 6 位配对码，然后点「连接设备」。')


def _pair_hint(ctx):
    """配对码模式下方那句操作提示。

    由当前模式现算，所以「手动配对 → 二维码 → 配对码」来回切、或从设备行的
    「配对」进来，同一模式看到的永远是同一句（用户报过文字会变）。
    """
    if ctx.get('pair_qr'):
        return ''                    # 二维码模式：提示画在码旁边，状态行留给等待/报错
    if ctx.get('pair_mode') == 'prefill':
        who = ctx.get('pair_name') or ctx.get('pair_label') or '这台设备'
        return ('「%s」还没配对：请在设备上打开「使用配对码配对设备」，'
                '把设备上显示的 6 位配对码填进「配对码」框，再点下面的「连接设备」。' % who)
    return PAIR_HINT_MANUAL


def _set_pair_open(ctx, mode='', addr=''):
    """开/关配对面板。mode: '' 收起 / 'prefill' 已填好 IP:端口 / 'manual' 手动填。

    prefill 时 IP、端口 两个框直接藏起来（已经填好，用户不用看也不用改），
    面板标题只说「填写配对码」；manual 时三个框都摆出来。

    面板只属于无线页：在 USB 页调进来也一律按收起处理——三个输入框是原生子窗口，
    不藏起来就会浮在 USB 提示文字上面（用户报过这个）。
    """
    on = bool(mode) and ctx.get('tab', 'wifi') == 'wifi'
    if not on:
        # 收起或离开无线页：正在等的扫码任务也作废，别再让后台线程等下去
        ctx['pair_qr'] = False
        ctx['qr_cancel'] = True
    ctx['pair_open'] = on
    ctx['pair_mode'] = mode
    ctx['pair_label'] = addr
    ctx['scroll'] = 0                # 面板高度变了，列表回到顶部，免得停在半截
    qr = on and bool(ctx.get('pair_qr'))
    show_ip = on and mode == 'manual' and not qr
    if show_ip:                     # 手动填：把上次留下的 IP/端口 清空，别让旧值误导
        for eid in (ID_IP, ID_PORT):
            eh = ctx['edits'].get(eid)
            if eh:
                user32.SendMessageW(eh, WM_SETTEXT, 0, ctypes.c_wchar_p(''))
    for eid, visible in ((ID_IP, show_ip), (ID_PORT, show_ip), (ID_CODE, on and not qr)):
        eh = ctx['edits'].get(eid)
        if eh:
            user32.ShowWindow(eh, 5 if visible else 0)   # SW_SHOW / SW_HIDE
    # 收起时窗口跟着矮下去（用户嫌底部空了一大片）
    _fit_client(ctx['hwnd'], WIN_W, _win_h(ctx))
    _rebuild_hotspots(ctx)
    if on:                           # 提示语由模式决定，任何入口进来都一致
        _status(ctx, _pair_hint(ctx), 'info')
    _invalidate(ctx)


def _set_pair_kind(ctx, qr):
    """面板里在「配对码 / 二维码」之间切换。

    进二维码：现生成一对服务名+口令，画码，然后开一个后台任务等设备扫；
    切回配对码随时可以（正在等扫码的那个任务会被取消），进二维码则要等手头
    的配对/刷新先结束，免得互相抢 adb。换码由二维码面板里的「刷新」负责。
    """
    if qr:
        if ctx.get('pair_qr'):
            return                  # 已经在二维码模式：换码请点面板里的「刷新」
        # 每 3 秒的静默刷新也在忙，但它马上就结束：照样让用户切到二维码（任务排队等它）。
        if ctx['busy'] and not ctx.get('auto_refresh'):
            return
    else:
        ctx['qr_cancel'] = True     # 让等待扫码的后台任务自己退出
        ctx['busy_text'] = ''
    if not qr:
        ctx['pair_qr'] = False
        _set_pair_open(ctx, ctx['pair_mode'] or 'manual', ctx['pair_label'])
        return
    if segno is None:
        _status(ctx, '这台电脑缺少二维码组件，先用配对码吧。', 'err')
        return
    service, password = qr_credentials()
    ctx['pair_qr'] = True
    ctx['qr_creds'] = (service, password)
    ctx['qr_rows'] = qr_rows(service, password)
    _set_pair_open(ctx, ctx['pair_mode'] or 'manual', ctx['pair_label'])
    _start_job(ctx, 'qr', (service, password), busy_text='等待设备扫码')


def _refresh_qr(ctx):
    """换一张新码：旧码作废，等待中的任务重新开始等新码。"""
    if not ctx.get('pair_qr'):
        return
    if ctx['busy'] and ctx.get('run_kind') != 'qr':
        return                      # 正在配对/连接时别换，免得打断（正在等扫码可以换）
    ctx['qr_cancel'] = True
    service, password = qr_credentials()
    ctx['qr_creds'] = (service, password)
    ctx['qr_rows'] = qr_rows(service, password)
    _rebuild_hotspots(ctx)
    _invalidate(ctx)
    _start_job(ctx, 'qr', (service, password), busy_text='等待设备扫码')


def _prefill_pair(ctx, row):
    """点「配对」：填好这一行的 IP、焦点交给配对码框（只差 6 位码）。

    **故意不填端口**：列表里那个 ip:端口 是投屏用的**连接端口**，而配对端口是另一个
    随机端口（手机「使用配对码配对设备」页面上显示的那个）。把连接端口填进去当配对
    端口用，配对必然失败——所以这里只留 IP，端口交给 `discover_pair_target` 现问设备。
    """
    ip = _ip_of(row.get('addr') or '')
    # 点的是具体某一台设备：从二维码模式回到配对码模式，别再让后台等着扫码
    ctx['pair_qr'] = False
    ctx['qr_cancel'] = True
    ctx['pair_name'] = row.get('name') or ''
    for eid, text in ((ID_IP, ip), (ID_PORT, '')):
        user32.SendMessageW(ctx['edits'][eid], WM_SETTEXT, 0,
                            ctypes.c_wchar_p(text))
    _set_pair_open(ctx, 'prefill' if ip else 'manual', ip)
    user32.SetFocus(ctx['edits'][ID_CODE])


def _remembered_name(row, known=()):
    """没连上的设备用「记住的名字」或 IP 兜底，绝不把 mDNS 机器串号当名字。

    用户看不懂 `adb-bfd7e543-MoDo9u` 里那截 MoDo9u；同一台机在历史里记过
    「一加 13」就沿用它。
    """
    ip = _ip_of(row.get('addr') or '')
    for k in known or []:
        same = (row.get('serial') and k.get('serial') == row['serial']) or \
               (ip and _ip_of(k.get('addr') or '') == ip)
        if same and not _is_junk_name(k.get('name'), k.get('serial')):
            return k['name']
    if ip:
        return '无线设备 ' + ip
    return '无线设备'


def _last_known_name(known):
    """历史里最近连过的那台设备叫什么（空列表里提一句用），没有就返回 ''。"""
    for k in known or ():
        name = (k.get('name') or '').strip()
        if not name or name.startswith('无线设备'):
            continue                 # 「无线设备 / 无线设备 <ip>」是兜底名，提了等于没说
        if not _is_junk_name(name, k.get('serial') or ''):
            return name
    return ''


def merge_devices(online, discovered, known):
    """三源合并成向导设备列表，按 在线 > 新发现 > 历史 排序，按身份去重。

    online:     parse_devices_l 的结果（在线，含 model）
    discovered: parse_mdns_all 的结果（mDNS 刚发现、尚未 connect）
    known:      CFG['known_devices']（魔芋记住的历史设备）
    返回 [{'serial','name','addr','kind','state'}]
      kind: 'usb' | 'wifi'；state: 'online' | 'found' | 'offline'
    """
    rows = []
    seen_serial = set()
    seen_addr = set()
    seen_ip = set()

    def _identity_key(addr, serial):
        # mDNS 串号与 ip:port 指向同一台手机时用 addr 去重
        return addr or serial

    def _claim(row, serial, addr):
        rows.append(row)
        seen_serial.add(serial)
        if addr:
            seen_addr.add(addr)
            seen_ip.add(_ip_of(addr))

    # mDNS 串号 → 它的 ip:port：adb 把在线设备报成 mDNS 串号时靠它认出 IP，
    # 这样「在线（mDNS 串号）」和「新发现（同一台机的 ip:port）」不会再显示成两行
    inst_addr = {}
    for d in discovered or []:
        if d.get('serial') and d.get('addr'):
            inst_addr[d['serial']] = d['addr']

    for d in online:
        serial = d['serial']
        addr = serial if IP_PORT_RE.match(serial) else inst_addr.get(serial, '')
        ip = _ip_of(addr) if addr else ''
        if ip and ip in seen_ip:      # 同一台机被 adb 报成两个串号（ip:port 与 mDNS 串号）
            continue
        _claim({'serial': serial, 'name': d.get('model') or serial,
                'addr': addr, 'kind': classify_device(serial), 'state': 'online'},
               serial, addr)
    for d in discovered or []:
        if d['serial'] in seen_serial:
            if d['addr']:
                seen_addr.add(d['addr'])
                seen_ip.add(_ip_of(d['addr']))
            continue
        if d['addr'] in seen_addr or _ip_of(d['addr']) in seen_ip:
            continue
        _claim({'serial': d['addr'], 'name': d['name'], 'addr': d['addr'],
                'kind': 'wifi', 'state': 'found'}, d['serial'], d['addr'])
    # 兜底：adb 把**同一台**手机报成两个串号（mDNS 串号 + ip:port）而拿不到 mDNS 的 IP 时，
    # 并成一行——投屏只连一台手机，用户不该看到两台「已连接」。
    wifi = [r for r in rows if r['kind'] == 'wifi' and r['state'] == 'online']
    if len(wifi) == 2:
        inst = next((r for r in wifi if '_tcp' in r['serial']), None)
        ipp = next((r for r in wifi if IP_PORT_RE.match(r['serial'] or '')), None)
        if inst is not None and ipp is not None and inst is not ipp:
            inst['addr'] = inst['addr'] or ipp['addr'] or ipp['serial']
            rows.remove(ipp)
            seen_serial.discard(ipp['serial'])
            seen_addr.add(inst['addr'])
            seen_ip.add(_ip_of(inst['addr']))
    for k in known or []:
        serial = k.get('serial') or ''
        addr = k.get('addr') or (serial if IP_PORT_RE.match(serial) else '')
        if serial in seen_serial or (addr and addr in seen_addr):
            continue
        # 同一台手机换了端口：IP 相同就视为同一台，不再重复列历史行
        ip = _ip_of(addr)
        if ip and ip in seen_ip:
            continue
        if serial in inst_addr and _ip_of(inst_addr[serial]) in seen_ip:
            continue
        # 旧配置里可能同时留着 ip:port 和 mDNS 串号两条记录：只留 mDNS 那条
        if IP_PORT_RE.match(serial) and _mdns_twin(known, ip) is not None:
            continue
        name = k.get('name') or ''
        if _is_junk_name(name, serial):
            name = _default_name(serial, addr)
        _claim({'serial': serial, 'name': name,
                'addr': addr, 'kind': classify_device(serial), 'state': 'offline'},
               serial, addr)
    return rows


def reconnect_known(adb, entry, runner=run_cmd):
    """历史设备一键重连：先试最后的端口，不行再 mDNS 找新端口。

    entry: {'serial','addr','name'}；返回 (ok, 通俗提示, 可用串号)。
    """
    serial = entry.get('serial') or ''
    addr = entry.get('addr') or ''
    if classify_device(serial) == 'usb':
        if serial in list_online(adb, runner):
            return True, '连接成功', serial
        return False, '这台 USB 设备当前不在线：请插好数据线，并在手机上允许 USB 调试。', ''
    # 1) mDNS 串号还在线就直接用
    if serial and serial in list_online(adb, runner):
        return True, '连接成功', serial
    # 2) 最后的 ip:port 再试一次（端口没变就能直接连上）
    if IP_PORT_RE.match(addr):
        ok, msg, s = connect_only(adb, addr, runner)
        if ok:
            return True, msg, s
    # 3) mDNS 找新端口（优先同 IP；手机换了 IP 时按 mDNS 服务名认回来）
    out = runner([adb, 'mdns', 'services'])[1]
    found = parse_mdns_services(out, _ip_of(addr))
    if not found and serial:
        for d in parse_mdns_all(out):
            if d['serial'] == serial:
                found = d['addr']
                break
    if found:
        ok, msg, s = connect_only(adb, found, runner)
        if ok:
            return True, msg, s
    return False, ('没找到这台手机：请确认手机「无线调试」还开着、和电脑在同一个 Wi-Fi。'
                   '若刚重开过无线调试，端口已变，请用「首次配对」重新连一次。'), ''


def try_auto_connect(adb, cfg_serial, runner=run_cmd):
    """启动时先自己找手机，找到就返回串号，找不到返回空串（此时才需要弹向导）。

    顺序：
      1. `adb devices` 里已有在线设备（USB 或上次连过的无线）→ 直接用；
      2. 配置里那个 `IP:端口` 再 connect 一次（端口没变就能直接连上）；
      3. `adb mdns services` 找 `_adb-tls-connect._tcp`（优先同 IP，其次任意一台）→ connect。
    这样「手机无线调试开着且之前配对过」的日常场景基本是零输入。
    """
    devs = list_online(adb, runner)
    if devs:
        return devs[0]
    cands = []
    cfg_serial = (cfg_serial or '').strip()
    if IP_PORT_RE.match(cfg_serial):
        cands.append(cfg_serial)
    ip = _ip_of(cfg_serial) if cands else ''
    rc, out = runner([adb, 'mdns', 'services'])
    found = parse_mdns_services(out, ip) or parse_mdns_services(out, '')
    if found and found not in cands:
        cands.append(found)
    for c in cands:
        ok, msg, serial = connect_only(adb, c, runner)
        if ok:
            return serial
    return ''


PAIR_DISCOVER_SECS = 10      # 配对成功后等手机广播「连接端口」的秒数
QR_WAIT_SECS = 120           # 扫码配对：等用户扫完码的最长时间（秒）
QR_SERVICE_PREFIX = 'MOYU-'
QR_NAME_LEN = 6              # 服务名随机后缀位数（手机把它当 mDNS 实例名的前缀广播回来）
QR_PASS_LEN = 10             # 配对口令位数
# 避开 0/o/1/l/i 这类看着像的字符：口令是照着屏幕核对的东西，别让用户自己看错
QR_NAME_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789'
QR_PASS_CHARS = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'


PAIR_SETUP_SECS = 6          # 在手机「使用配对码配对设备」页面等配对端口的秒数


_IP_RE = re.compile(r'^(?:\d{1,3}\.){3}\d{1,3}$')


def _is_ip(text):
    """是不是一个合法的 IPv4（'...'、'1.2.3.4.5' 这类要挡掉，别拿去当单播目标）。"""
    if not _IP_RE.match(text or ''):
        return False
    return all(0 <= int(p) <= 255 for p in text.split('.'))


def _ip_only(text):
    """把用户填的东西取成纯 IP：'192.168.1.5:43847' 和 '192.168.1.5' 都行。"""
    text = (text or '').strip()
    ip = _ip_of(text) if IP_PORT_RE.match(text) else text
    return ip if _is_ip(ip) else ''


def discover_pair_target(peers=(), timeout=PAIR_SETUP_SECS, browser=None,
                         name=''):
    """自动找手机的**配对端口**，返回 'ip:port'；找不到返回 ''。

    手机停在「使用配对码配对设备」页面时会广播 `_adb-tls-pairing._tcp`，
    但安卓的 NsdManager 广播**不回组播查询**，必须单播问手机 ip:5353 ——
    所以先用连接广播把手头没有的手机 IP 补齐（它组播就能问到），再逐台单播问配对端口。
    browser: 可注入（默认 mdns_browse.browse），返回 [{'name','host','port','addr'}]。
    name: 只认实例名里含这段文字的那条广播（扫码配对时用我们自己的服务名匹配）。
    """
    if mdns_browse is None and browser is None:
        return ''
    browse = browser or mdns_browse.browse
    ips = []
    for ip in peers:
        if ip and ip not in ips and _is_ip(ip):
            ips.append(ip)
    # 连接广播（`_adb-tls-connect._tcp`）组播就有回包：它能把「历史地址过期了」补回来，
    # 所以每次都要问（1.5 秒），不能因为手头有旧 IP 就跳过
    for it in browse('_adb-tls-connect._tcp', timeout=min(1.5, timeout), targets=[]):
        ip = it.get('addr') or ''
        if ip and ip not in ips and _is_ip(ip):
            ips.append(ip)
    for it in browse('_adb-tls-pairing._tcp', timeout=timeout, targets=ips):
        if name and name not in (it.get('name') or ''):
            continue
        ip, port = it.get('addr') or '', it.get('port') or 0
        if _is_ip(ip) and port:
            return '%s:%s' % (ip, port)
    return ''


def pair_then_connect(adb, ip_port, code, runner=run_cmd, log=None,
                      wait=PAIR_DISCOVER_SECS):
    """一键「连接设备」：配对 + 自动找到真正的「连接端口」再连，用户无感。

    手机无线调试有两个端口：配对弹窗里的**配对端口**和主页面上「IP 地址和端口」的
    **连接端口**，两者通常不同——配对成功后不能拿配对端口去 connect。
    连接端口只能靠手机的 mDNS 广播（`_adb-tls-connect._tcp`）发现：广播可能迟到，
    路由器开了客户端隔离时甚至永远收不到。所以这里：
      1. 配对成功后先看 `adb devices`（mDNS 常常直接出现可用设备）；
      2. 不行再查 `adb mdns services` 拿该 IP 的连接端口，然后 connect；
      3. 重试 wait 秒；仍不行就把「端口框手动填一次」的办法说清楚。
    log: 可选日志回调；放弃时写原始 mdns 输出，便于以后定位。
    """
    ok, msg = pair_only(adb, ip_port, code, runner)
    if not ok:
        return False, msg, ''
    ip = _ip_of(ip_port)
    seen_addr, last_err, out = '', '', ''
    # mDNS 广播有延迟：多等几轮，尽量不让用户做任何额外操作
    for _ in range(max(1, wait)):
        devs = list_online(adb, runner)
        if devs:
            return True, '配对并连接成功', devs[0]
        out = runner([adb, 'mdns', 'services'])[1]
        serial = parse_mdns_services(out, ip)
        if serial:
            ok2, msg2, s2 = connect_only(adb, serial, runner)
            if ok2:
                return True, '配对并连接成功', s2
            seen_addr, last_err = serial, msg2   # 端口找到了但连不上，不能报「找不到端口」
        time.sleep(1.0)
    if log:
        log('wizard: 配对成功但没连上，最后 mdns=%r' % ((out or '')[:600],))
    if seen_addr:
        return False, ('配对成功了，端口也找到了（%s），但连接没成功：%s'
                       % (seen_addr, last_err or '手机没有响应。')), ''
    return False, ('配对成功了，但没收到手机的连接广播。请回到手机「无线调试」主页'
                   '（能看到「IP 地址和端口」那一页）停留几秒让电脑自动连；'
                   '若还是连不上，把那一页上的「端口」填到上面的端口框、配对码留空，'
                   '再点一次「连接设备」。'), ''


def qr_credentials(rnd=None):
    """扫码配对用的服务名后缀和口令：都是我们说了算，手机只负责把名字广播回来。"""
    r = rnd or random.SystemRandom()
    service = QR_SERVICE_PREFIX + ''.join(r.choice(QR_NAME_CHARS) for _ in range(QR_NAME_LEN))
    password = ''.join(r.choice(QR_PASS_CHARS) for _ in range(QR_PASS_LEN))
    return service, password


def qr_payload(service, password):
    """二维码里的载荷格式（安卓「扫描二维码」页认的就是这个）。"""
    return 'WIFI:T:ADB;S:%s;P:%s;;' % (service, password)


def qr_rows(service, password):
    """把载荷算成二维码矩阵（每行一串 0/1，已含 4 模块静区）；没有 segno 时返回 []。"""
    if segno is None:
        return []
    qr = segno.make(qr_payload(service, password), error='m')
    return [tuple(row) for row in qr.matrix_iter(border=4)]


def qr_pair(adb, service, password, runner=run_cmd, log=None, wait=QR_WAIT_SECS,
            browser=None, cancel=None, peers=()):
    """扫码配对：等手机扫我们生成的码，拿到它广播的配对端口后配对 + 连接。

    手机扫完码会用**我们的服务名**广播 `_adb-tls-pairing._tcp`（端口随机），
    所以这里按名字匹配而不是按 IP——用户不用告诉电脑手机在哪。
    cancel: 可选回调，返回 True 表示用户在界面上已经放弃（关窗/切回配对码）。
    peers: 已知的手机 IP，拿来单播问配对端口（组播被路由器挡住时的兜底）。
    """
    deadline = time.time() + max(1, wait)
    while time.time() < deadline:
        if cancel and cancel():
            return False, '已取消扫码配对。', ''
        target = discover_pair_target(peers, timeout=2, browser=browser, name=service)
        if target:
            if log:
                log('wizard: 扫码配对目标 %r' % (target,))
            return pair_then_connect(adb, target, password, runner, log)
        time.sleep(0.5)
    return False, ('没扫到码。请让设备停在「使用配对码配对设备」页面，点「扫描二维码」'
                   '对着电脑上的码扫一次，扫上后再等 1-2 秒。'), ''


def decide_action(ip, port, code):
    """「连接设备」按钮该干什么：有配对码 → 配对+连接；只填 IP:端口 → 直接连接。

    返回 (kind, payload, 提示)：kind 是 'pair' / 'connect' / ''，
    为空串表示输入不合格，此时提示是中文错误文案。
    配对码留空＝已配对过，用户手填连接端口即可（mDNS 收不到时的兜底）。
    **只填了配对码**时 payload 的 ip_port 是空串：电脑自己去手机广播里找配对端口。
    """
    ip_port = join_ip_port(ip, port)
    code = (code or '').strip()
    if code:
        if not valid_code(code):
            return '', None, ('配对码是手机「使用配对码配对设备」页面上显示的 6 位数字；'
                              '已经配对过可以留空，直接点「连接设备」。')
        if not ip_port:
            return 'pair', ('', code), ''    # 端口交给电脑自动发现
        return 'pair', (ip_port, code), ''
    if not ip_port:
        return '', None, ('先在手机上打开「使用配对码配对设备」页面并把 6 位配对码填进'
                          '「配对码」框；或者按那一页显示的 IP 和端口填进前两个框。')
    return 'connect', ip_port, ''


def _default_name(serial, addr=''):
    """想不出名字时的兜底：绝不把串号/ip:port 当名字显示给用户。"""
    if addr and IP_PORT_RE.match(addr):
        return '无线设备 %s' % _ip_of(addr)
    if '_tcp' in (serial or ''):
        return '无线设备'
    return serial or '设备'


def _mdns_twin(known, ip):
    """历史记录里那台 mDNS 串号的手机（只有一台 wifi 手机时才有意义）。"""
    mdns = [k for k in known if '_tcp' in (k.get('serial') or '')]
    if len(mdns) != 1:
        return None
    twin = mdns[0]
    twin_ip = _ip_of(twin.get('addr') or '')
    if ip and twin_ip and twin_ip != ip:
        return None      # 明确是两台不同的手机
    return twin


def remember_device(cfg, serial, name, addr='', now=None):
    """把成功连过的设备写进 cfg['known_devices']（按身份去重，最多 8 台，新在前）。

    身份优先用 mDNS 串号（稳定），ip:port 只是易变信息：
    新记的是 ip:port 时并进同一台手机的 mDNS 记录，绝不单独再记一条。
    返回更新后的列表（原地改 cfg）。now 可注入便于测试。
    """
    known = cfg.get('known_devices')
    if not isinstance(known, list):
        known = []
    addr = addr or (serial if IP_PORT_RE.match(serial or '') else '')
    ip = _ip_of(addr)
    name = '' if _is_junk_name(name, serial) else name
    stamp = int(now if now is not None else time.time())
    if IP_PORT_RE.match(serial or ''):
        twin = _mdns_twin(known, ip)
        if twin is not None:
            twin['addr'] = addr or twin.get('addr') or ''
            if _is_junk_name(twin.get('name'), twin.get('serial')):
                twin['name'] = name or _default_name(twin['serial'], twin['addr'])
            twin['last_seen'] = stamp
            known.sort(key=lambda k: -(k.get('last_seen') or 0))
            cfg['known_devices'] = known
            return known
    entry = {'serial': serial, 'name': name or _default_name(serial, addr),
             'addr': addr, 'last_seen': stamp}
    rest = []
    for k in known:
        if k.get('serial') == serial:
            continue
        if ip and _ip_of(k.get('addr') or '') == ip:
            continue   # 同一台手机换了端口：旧记录作废
        kser = k.get('serial') or ''
        if '_tcp' in (serial or '') and IP_PORT_RE.match(kser):
            auto = not k.get('name') or k['name'] in (kser, _default_name(kser, kser))
            if auto or (ip and _ip_of(kser) == ip):
                continue   # 有了 mDNS 串号，同一台手机的 ip:port 记录就没用了
        rest.append(k)
    known = [entry] + rest[:7]
    cfg['known_devices'] = known
    return known


# ---------------------------------------------------------------------------
# UI 层：自绘向导
# ---------------------------------------------------------------------------

# 配色：暖白底 + 墨青主色 + 琥珀点缀，语义色齐全（与主程序冷灰操作栏同族但更暖）
C_BG = (247, 246, 243)          # 暖白底
C_SURFACE = (255, 255, 255)     # 卡片白
C_INK = (32, 38, 46)            # 主文字 墨青黑
C_SUB = (108, 116, 128)         # 次级文字
C_FAINT = (156, 163, 173)       # 弱化文字/占位
C_LINE = (228, 226, 220)        # 分隔线
C_PRIMARY = (23, 152, 140)      # 主色 浅青绿（连接/选中；再浅一档，白字对比仍 ≥3:1）
C_PRIMARY_DK = (18, 124, 114)   # 主色 按下
C_PRIMARY_HOVER = (12, 104, 96)  # 主色 hover：明显深一档（和大按钮共用）
C_PRIMARY_LT = (230, 246, 243)  # 主色 浅底（选中 tab 底/徽标底）
C_ACCENT = (217, 119, 6)        # 琥珀（新发现徽标/提示图标）
C_ACCENT_LT = (253, 240, 222)
C_OK = (22, 163, 74)            # 在线
C_OK_LT = (226, 246, 233)
C_ERR = (200, 44, 44)           # 错误红
C_ERR_LT = (252, 235, 235)
C_HOVER = (240, 239, 235)       # 行 hover
C_EDIT_BG = (255, 255, 255)
C_EDIT_LINE = (203, 201, 195)
C_EDIT_FOCUS = (23, 152, 140)
C_CUE = (163, 161, 155)           # 输入框 placeholder 灰字


def _rgb(c):
    return c[0] | (c[1] << 8) | (c[2] << 16)


# 布局常量（客户区坐标，DPI 未缩放时按 96dpi 设计；窗口固定尺寸）
WIN_W, WIN_H = 520, 560
WIN_H_CLOSED = 508                   # 配对面板收起时窗口矮一点，底部不留大片空白
PAD = 20
TAB_Y, TAB_H = 56, 34
LIST_Y = TAB_Y + TAB_H + 14
ROW_H = 52
MAX_ROWS = 4
LIST_H = ROW_H * MAX_ROWS
MAX_ROWS_FULL = 6                      # 配对面板收起时列表占满下方空白，能多看两台
LIST_H_FULL = ROW_H * MAX_ROWS_FULL
SEC_Y = LIST_Y + LIST_H + 16          # 配对面板标题（收起时是「手动配对」链接）
IP_Y = SEC_Y + 38                      # 与上面「配对码/二维码」那一行留出呼吸空间
CODE_Y = IP_Y + 40
GO_Y = CODE_Y + 44
MANUAL_TEXT = '设备不在列表里？手动配对'
SUCCESS_CLOSE_MS = 1000         # 「连接成功」toast 停留时长，之后窗口自动关闭
AUTO_REFRESH_MS = 3000          # 向导开着时每 3 秒悄悄刷新一次设备列表
AUTO_REFRESH_TIMER = 3          # 该刷新用的 WM_TIMER id
GO_H = 40
# 扫码模式：没有输入框和大按钮，面板里摆二维码 + 一句提示，窗口跟着变高
QR_SIZE = 148                          # 二维码边长（37 模块 × 4px，整数倍最清晰）
WIN_H_QR = 572                         # 二维码模式：摆得下码 + 一行状态，底部不留空白
MODE_CHIP_W, MODE_CHIP_H = 60, 24      # 「配对码 / 二维码」小分段按钮（配色同上方 tab，字不加粗）
MODE_CHIP_Y = SEC_Y - 2
USB_HINT_Y = LIST_Y + LIST_H_FULL + 16
EDIT_X = PAD + 64                     # 输入框左缘（标签右侧）
IP_W = 176                            # IP 框宽（适量，不拉满）
COLON_GAP = 12                        # 冒号与左右输入框的间距（两侧一致）
PORT_W = 104                          # 端口框宽
# 原生单行 EDIT 文字 ink 固定在 y5..15（中心 10），框高取 20 即天然垂直居中；
# EM_SETRECT 对单行 EDIT 无效（实测），多行方案会吞回车，都不用。
EDIT_H = 20


def _font_create(px, weight=400):
    # 微软雅黑：中文渲染最稳；负高度 = 字符高度（不含行距）
    return gdi32.CreateFontW(-px, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, '微软雅黑')


def _text_size(hdc, font, text):
    old = gdi32.SelectObject(hdc, font)
    sz = wt.SIZE()
    gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
    gdi32.SelectObject(hdc, old)
    return sz.cx, sz.cy


def _draw_text(hdc, font, x, y, text, color):
    old = gdi32.SelectObject(hdc, font)
    gdi32.SetTextColor(hdc, _rgb(color))
    gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
    gdi32.TextOutW(hdc, x, y, text, len(text))
    gdi32.SelectObject(hdc, old)


def _fill_rect(hdc, x, y, w, h, color):
    br = gdi32.CreateSolidBrush(_rgb(color))
    rc = wt.RECT(x, y, x + w, y + h)
    user32.FillRect(hdc, ctypes.byref(rc), br)
    gdi32.DeleteObject(br)


def _round_rect(hdc, x, y, w, h, r, fill, line=None):
    br = gdi32.CreateSolidBrush(_rgb(fill))
    pen = gdi32.CreatePen(0, 1, _rgb(line or fill))
    ob = gdi32.SelectObject(hdc, br)
    op = gdi32.SelectObject(hdc, pen)
    gdi32.RoundRect(hdc, x, y, x + w, y + h, r, r)
    gdi32.SelectObject(hdc, ob)
    gdi32.SelectObject(hdc, op)
    gdi32.DeleteObject(br)
    gdi32.DeleteObject(pen)


def _dot(hdc, x, y, d, color):
    br = gdi32.CreateSolidBrush(_rgb(color))
    pen = gdi32.CreatePen(0, 1, _rgb(color))
    ob = gdi32.SelectObject(hdc, br)
    op = gdi32.SelectObject(hdc, pen)
    gdi32.Ellipse(hdc, x, y, x + d, y + d)
    gdi32.SelectObject(hdc, ob)
    gdi32.SelectObject(hdc, op)
    gdi32.DeleteObject(br)
    gdi32.DeleteObject(pen)


def _hline(hdc, x, y, w, color):
    pen = gdi32.CreatePen(0, 1, _rgb(color))
    op = gdi32.SelectObject(hdc, pen)
    gdi32.MoveToEx(hdc, x, y, None)
    gdi32.LineTo(hdc, x + w, y)
    gdi32.SelectObject(hdc, op)
    gdi32.DeleteObject(pen)


def _badge(hdc, font, x, y, text, fg, bg):
    w, h = _text_size(hdc, font, text)
    bw, bh = w + 14, h + 6
    _round_rect(hdc, x, y, bw, bh, 10, bg)
    _draw_text(hdc, font, x + 7, y + 3, text, fg)
    return bw


def _text(hwnd):
    """读控件文字（EDIT）。"""
    n = int(user32.GetWindowTextLengthW(hwnd) or 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _status(ctx, text, kind='info'):
    """内联状态：info 灰 / ok 绿 / err 红 / busy 主色。不弹系统 MessageBox。"""
    ctx['status_text'] = text
    ctx['status_kind'] = kind
    _invalidate(ctx)


def _invalidate(ctx):
    user32.InvalidateRect(ctx['hwnd'], None, False)


def _xy(lparam):
    """WM_MOUSE* 的 lparam → 有符号客户区坐标（窗口外拖动时高位是负数）。"""
    x, y = lparam & 0xFFFF, (lparam >> 16) & 0xFFFF
    return x - 0x10000 if x >= 0x8000 else x, y - 0x10000 if y >= 0x8000 else y


def _hit(ctx, x, y):
    """客户区坐标 → 可点元素 id；没有则 0。

    设备行本身不可点：只有行右侧的「连接」/「配对」按钮有热点。
    已连接与离线的设备连按钮都没有，点它什么也不会发生。
    """
    for hid, (rx, ry, rw, rh) in ctx['hotspots'].items():
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return hid
    return 0


def _list_rows(ctx):
    """当前标签页的设备行（全部，滚动只看其中一段）。

    只列**现在能操作**的设备：已连接（点托盘即可投屏）、新发现（能连接或配对）。
    「离线」= 手机没开无线调试，此刻既配不了也连不上；而它一旦打开无线调试，就会
    以「新发现」重新出现——所以离线行只会是死按钮，不显示。
    """
    kind = 'usb' if ctx.get('tab') == 'usb' else 'wifi'
    return [r for r in ctx['rows'] if r['kind'] == kind and r['state'] != 'offline']


def _max_rows(ctx):
    """列表一次能显示几行：面板展开时 4 行，收起时 6 行。"""
    return MAX_ROWS if ctx.get('pair_open') else MAX_ROWS_FULL


def _win_h(ctx):
    """窗口高度：面板收起时矮一点；二维码模式要摆下二维码，比配对码模式更高。"""
    if not ctx.get('pair_open'):
        return WIN_H_CLOSED
    return WIN_H_QR if ctx.get('pair_qr') else WIN_H


def _fit_client(hwnd, w, h):
    """把窗口调成「客户区正好 w x h」（SetWindowPos 给的是含边框的窗口尺寸）。"""
    rc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    wr = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    dw = (wr.right - wr.left) - rc.right
    dh = (wr.bottom - wr.top) - rc.bottom
    user32.SetWindowPos(hwnd, None, 0, 0, w + dw, h + dh,
                        0x0002 | 0x0004 | 0x0010)   # NOMOVE|NOZORDER|NOACTIVATE


def _list_h(ctx):
    """设备列表卡片高度固定（不随行数变化），行数多了用滚轮翻。"""
    return ROW_H * _max_rows(ctx)


def _max_scroll(ctx):
    return max(0, len(_list_rows(ctx)) - _max_rows(ctx))


def _visible_rows(ctx):
    """当前滚到哪一段。"""
    rows = _list_rows(ctx)
    top = min(max(0, ctx.get('scroll') or 0), max(0, len(rows) - 1))
    return rows[top:top + _max_rows(ctx)], top


def _scroll_by(ctx, delta):
    """滚动列表；返回是否真的动了（没动就不必重画）。"""
    before = ctx.get('scroll') or 0
    ctx['scroll'] = min(max(0, before + delta), _max_scroll(ctx))
    if ctx['scroll'] == before:
        return False
    _rebuild_hotspots(ctx)
    _invalidate(ctx)
    return True


def _pair_link_y(ctx):
    """「手动配对」链接紧跟在列表卡片下沿。"""
    return LIST_Y + _list_h(ctx) + 16


def _rebuild_hotspots(ctx):
    hs = {}
    hs[ID_TAB_WIFI] = (PAD, TAB_Y, 108, TAB_H)
    hs[ID_TAB_USB] = (PAD + 116, TAB_Y, 108, TAB_H)
    ctx['visible_rows'], _top = _visible_rows(ctx)
    for i, r in enumerate(ctx['visible_rows']):
        y = LIST_Y + i * ROW_H
        # 已连接的不用点；「新发现」有按钮（以前连过→连接，没连过→配对）
        if r['state'] == 'found':
            hs[ID_ROW_BASE + i] = (WIN_W - PAD - 76, y + 10, 64, 32)
    hs[ID_REFRESH] = (WIN_W - PAD - 64, TAB_Y + 4, 64, 26)
    if ctx['tab'] == 'wifi':
        if ctx.get('pair_open'):
            if not ctx.get('pair_qr'):
                hs[ID_GO] = (PAD, GO_Y, WIN_W - 2 * PAD, GO_H)
            else:                       # 二维码右侧的「刷新」文字按钮
                hs[ID_QR_REFRESH] = (PAD + QR_SIZE + 16, IP_Y + QR_SIZE - 22, 48, 24)
            # 面板右上一排：［配对码］［二维码］ 收起
            close_x = WIN_W - PAD - 40
            qr_x = close_x - 10 - MODE_CHIP_W
            code_x = qr_x - 6 - MODE_CHIP_W
            hs[ID_MODE_CODE] = (code_x, MODE_CHIP_Y, MODE_CHIP_W, MODE_CHIP_H)
            hs[ID_MODE_QR] = (qr_x, MODE_CHIP_Y, MODE_CHIP_W, MODE_CHIP_H)
            hs[ID_PAIR_CLOSE] = (close_x, MODE_CHIP_Y, 40, MODE_CHIP_H)
        else:
            hs[ID_MANUAL] = (PAD, _pair_link_y(ctx) - 4, 196, 26)
    ctx['hotspots'] = hs


def _draw_qr(hdc, ctx, x, y):
    """把二维码矩阵画成一格格方块（矩阵里已含 4 模块静区）。"""
    rows = ctx.get('qr_rows') or []
    if not rows:
        return
    n = len(rows)
    s = max(1, QR_SIZE // n)          # 整数倍缩放，屏幕上看不出锯齿
    side = s * n
    _fill_rect(hdc, x, y, side, side, C_SURFACE)
    for j, row in enumerate(rows):
        for i, dark in enumerate(row):
            if dark:
                _fill_rect(hdc, x + i * s, y + j * s, s, s, C_INK)


def _paint(ctx, hdc):
    W, H = WIN_W, _win_h(ctx)
    # 按真实客户区铺底：窗口高矮偶尔和我们算的差几像素（边框/DPI 取整），
    # 少铺一条就会在底部留出黑色（用户报过二维码模式下方有黑边）。
    rc = wt.RECT()
    if user32.GetClientRect(ctx['hwnd'], ctypes.byref(rc)) and rc.bottom > H:
        H = rc.bottom
    _fill_rect(hdc, 0, 0, W, H, C_BG)
    f_title = ctx['f_title']
    f_body = ctx['f_body']
    f_small = ctx['f_small']
    f_btn = ctx['f_btn']

    # 标题区
    _draw_text(hdc, f_title, PAD, 16, '连接设备', C_INK)
    _draw_text(hdc, f_small, PAD + 92, 24, '连上后自动开始投屏', C_FAINT)

    # Tab 栏（分段式）
    for tid, label in ((ID_TAB_WIFI, '无线连接'), (ID_TAB_USB, 'USB 连接')):
        x, y, w, h = ctx['hotspots'][tid]
        active = (tid == ID_TAB_WIFI and ctx['tab'] == 'wifi') or \
                 (tid == ID_TAB_USB and ctx['tab'] == 'usb')
        hover = ctx['hover'] == tid
        tw, th = _text_size(hdc, f_btn, label)
        tx = x + (w - tw) // 2
        ty = y + (h - th) // 2
        if active:
            _round_rect(hdc, x, y, w, h, 10, C_PRIMARY)
            _draw_text(hdc, f_btn, tx, ty, label, (255, 255, 255))
        else:
            _round_rect(hdc, x, y, w, h, 10, C_HOVER if hover else C_SURFACE,
                        C_LINE)
            _draw_text(hdc, f_btn, tx, ty, label, C_SUB if not hover else C_INK)

    # 刷新按钮（右上角小按钮，纯文字）
    rx, ry, rw, rh = ctx['hotspots'][ID_REFRESH]
    hover = ctx['hover'] == ID_REFRESH
    fg = C_PRIMARY if hover else C_SUB
    _round_rect(hdc, rx, ry, rw, rh, 8, C_PRIMARY_LT if hover else C_SURFACE, C_LINE)
    tw = _text_size(hdc, f_small, '刷新')[0]
    _draw_text(hdc, f_small, rx + (rw - tw) // 2, ry + 5, '刷新', fg)

    # 设备列表卡片
    rows = ctx['visible_rows']
    list_h = _list_h(ctx)
    _round_rect(hdc, PAD, LIST_Y - 6, W - 2 * PAD, list_h + 12, 12, C_SURFACE, C_LINE)
    if not rows:
        if ctx['tab'] == 'wifi':
            msg1 = '还没有无线设备'
            last = _last_known_name(ctx.get('known') or ())
            if last:
                msg2 = '上次连过的「%s」现在没开着无线调试；' % last
                msg3 = ('打开「无线调试」它就会自动出现。' if ctx['pair_open']
                        else '打开「无线调试」它就会自动出现，或点下方「手动配对」。')
            else:
                msg2 = '手机打开「无线调试」后会自动出现在这里；'
                msg3 = ('第一次使用请在下面手动填 IP 和配对码。' if ctx['pair_open']
                        else '第一次使用请点下方「手动配对」。')
        else:
            msg1 = '没有检测到 USB 设备'
            msg2 = '用数据线连接手机，并在手机上'
            msg3 = '允许「USB 调试」，然后点右上角「刷新」。'
        cx = W // 2
        for i, (m, f, c) in enumerate(((msg1, f_body, C_SUB), (msg2, f_small, C_FAINT),
                                       (msg3, f_small, C_FAINT))):
            tw = _text_size(hdc, f, m)[0]
            _draw_text(hdc, f, cx - tw // 2, LIST_Y + (list_h - 3 * 24) // 2 + i * 24, m, c)
    for i, r in enumerate(rows):
        y = LIST_Y + i * ROW_H
        # hover 只作用于「连接」按钮本身，设备行不做整行高亮
        if i:
            _hline(hdc, PAD + 14, y, W - 2 * PAD - 28, C_LINE)
        # 名称（营销名/型号，绝不显示 mDNS 串号）；过长先截断，给后面的徽标留位
        name = r['name']
        dot_c = {'online': C_OK, 'found': C_ACCENT}[r['state']]
        # 徽标只有「已连接」一种：没连上的行不挂标签（点它是去配对，不是状态差异）
        label, fg, bg = (('已连接', C_OK, C_OK_LT) if r['state'] == 'online'
                         else ('', None, None))
        bw = (_text_size(hdc, f_small, label)[0] + 14) if label else 0
        right = WIN_W - PAD - (14 if r['state'] == 'online' else 76 + 12)
        max_name_w = right - (PAD + 34) - bw - 8
        while name and _text_size(hdc, f_body, name + '…')[0] > max_name_w:
            name = name[:-1]
        if name != r['name']:
            name += '…'
        nx = PAD + 34
        # 副行地址：有则名称+地址两行整体垂直居中，无则名称单行垂直居中
        sub = r['addr'] or ('USB 数据线' if r['kind'] == 'usb' else '')
        if sub:
            block_h = 20 + 2 + 16          # 名称行高 + 行距 + 地址行高
            name_y = y + (ROW_H - block_h) // 2
            sub_y = name_y + 22
        else:
            name_y = y + (ROW_H - 20) // 2
            sub_y = 0
        # 状态点与名称行同一垂直中线（名称墨迹中心约在 name_y+10）
        _dot(hdc, PAD + 16, name_y + 6, 8, dot_c)
        _draw_text(hdc, f_body, nx, name_y, name, C_INK)
        # 状态徽标紧跟设备名同一行，与名称文字垂直居中对齐
        nw = _text_size(hdc, f_body, name)[0]
        if label:
            bh = _text_size(hdc, f_small, label)[1] + 6
            _badge(hdc, f_small, nx + nw + 8, name_y + (20 - bh) // 2, label, fg, bg)
        if sub:
            _draw_text(hdc, f_small, nx, sub_y, sub, C_FAINT)
        # 行尾按钮：已连接的设备不摆按钮（点了也是重复连接）；没连上的一律「配对」——
        # 它现在没连上，要重新连就得先配对（配对端口电脑自己去问设备）。
        if r['state'] != 'found':
            continue
        bx, by, bw, bh = ctx['hotspots'][ID_ROW_BASE + i]
        hover = ctx['hover'] == ID_ROW_BASE + i
        busy = ctx['busy'] and ctx['busy_row'] == i
        if busy:
            _round_rect(hdc, bx, by, bw, bh, 8, C_PRIMARY_LT)
            tw, th = _text_size(hdc, f_small, '连接中')
            _draw_text(hdc, f_small, bx + (bw - tw) // 2, by + (bh - th) // 2,
                       '连接中', C_PRIMARY)
        else:
            # 没连上的设备只能先配对：按钮就叫「配对」，点了展开配对面板填 6 位码
            # hover 与下方「连接设备」大按钮完全一致：主色填深一档 + 白字
            _round_rect(hdc, bx, by, bw, bh, 8,
                        C_PRIMARY_HOVER if hover else C_PRIMARY)
            tw, th = _text_size(hdc, f_btn, '配对')
            _draw_text(hdc, f_btn, bx + (bw - tw) // 2, by + (bh - th) // 2,
                       '配对', (255, 255, 255))

    # 设备多于能显示的行数时，右侧画一条细滚动条（滚轮滚动）
    total = len(_list_rows(ctx))
    if total > len(rows):
        track_x = W - PAD - 8
        track_y, track_h = LIST_Y + 4, list_h - 8
        _round_rect(hdc, track_x, track_y, 4, track_h, 2, C_LINE)
        thumb_h = max(24, track_h * len(rows) // total)
        top = ctx.get('scroll') or 0
        span = max(1, total - len(rows))
        thumb_y = track_y + (track_h - thumb_h) * top // span
        _round_rect(hdc, track_x, thumb_y, 4, thumb_h, 2, C_FAINT)

    if ctx['tab'] == 'wifi':
        if not ctx['pair_open']:
            # 平时收起配对面板：手机不在列表里（mDNS 收不到）时才需要手动填 IP:端口
            hx, hy, hw, hh = ctx['hotspots'][ID_MANUAL]
            _draw_text(hdc, f_body, PAD, hy + 2, MANUAL_TEXT,
                       C_PRIMARY_HOVER if ctx['hover'] == ID_MANUAL else C_PRIMARY)
            _draw_text(hdc, f_small, PAD + 210, hy + 5,
                       '设备不在附近、或者找不到时，手动填 IP 和配对码', C_FAINT)
        else:
            qr = bool(ctx.get('pair_qr'))
            prefill = ctx['pair_mode'] == 'prefill'
            _draw_text(hdc, ctx['f_bold'], PAD, SEC_Y,
                       '填写配对码' if (prefill and not qr) else '配对设备',
                       C_INK)
            # 右上一排：［配对码］［二维码］ 收起（配色/active 同上方 tab，文字不加深）
            for mid, mlabel in ((ID_MODE_CODE, '配对码'), (ID_MODE_QR, '二维码')):
                mx, my, mw, mh = ctx['hotspots'][mid]
                mtw, mth = _text_size(hdc, f_small, mlabel)
                mtx = mx + (mw - mtw) // 2
                mty = my + (mh - mth) // 2
                if (mid == ID_MODE_QR) == qr:
                    _round_rect(hdc, mx, my, mw, mh, 10, C_PRIMARY)
                    _draw_text(hdc, f_small, mtx, mty, mlabel, (255, 255, 255))
                else:
                    mhover = ctx['hover'] == mid
                    _round_rect(hdc, mx, my, mw, mh, 10,
                                C_HOVER if mhover else C_SURFACE, C_LINE)
                    _draw_text(hdc, f_small, mtx, mty, mlabel,
                               C_SUB if not mhover else C_INK)
            hx, hy, hw, hh = ctx['hotspots'][ID_PAIR_CLOSE]
            _draw_text(hdc, f_small, hx, hy + (hh - _text_size(hdc, f_small, '收起')[1]) // 2,
                       '收起',
                       C_PRIMARY_HOVER if ctx['hover'] == ID_PAIR_CLOSE else C_SUB)
            if qr:
                _draw_qr(hdc, ctx, PAD, IP_Y)
                hint = ('设备上打开「无线调试 → 使用配对码配对设备 → 扫描二维码」，'
                        '对着左边的码扫一次，电脑会自动配对并连接。')
                for i, ln in enumerate(_wrap_text(hdc, f_small, hint,
                                                  WIN_W - PAD - (PAD + QR_SIZE + 16))[:6]):
                    _draw_text(hdc, f_small, PAD + QR_SIZE + 16, IP_Y + 6 + i * 20, ln, C_SUB)
                # 码下面的「刷新」：换一张新码（旧码随即作废）
                fx, fy, fw, fh = ctx['hotspots'][ID_QR_REFRESH]
                fhover = ctx['hover'] == ID_QR_REFRESH
                _draw_text(hdc, f_small, fx, fy + (fh - _text_size(hdc, f_small, '刷新')[1]) // 2,
                           '刷新', C_PRIMARY if fhover else C_SUB)
            else:
                if prefill:
                    # 已知的设备地址摆在配对码框正上方：标签与「配对码」左对齐，值跟输入框对齐。
                    # 只有 IP（配对端口要到配对时才问设备）就只写「IP」。
                    label = ctx['pair_label'] or ''
                    _draw_text(hdc, f_small, PAD, CODE_Y - 24,
                               'IP和端口：' if ':' in label else 'IP：', C_SUB)
                    _draw_text(hdc, f_small, EDIT_X, CODE_Y - 24,
                               label, C_SUB)
                _draw_text(hdc, f_small, PAD, CODE_Y + 2, '配对码', C_SUB)
                focus = user32.GetFocus()
                targets = ((ID_CODE, CODE_Y),)
                if not prefill:
                    _draw_text(hdc, f_small, PAD, IP_Y + 2, 'IP 端口', C_SUB)
                    # IP 与端口之间的冒号（两个输入框同一行，用户不用输「:」）
                    cw, ch = _text_size(hdc, f_body, ':')
                    colon_x = EDIT_X + IP_W + (2 * COLON_GAP - cw) // 2
                    _draw_text(hdc, f_body, colon_x, IP_Y + (EDIT_H - ch) // 2, ':', C_SUB)
                    targets = ((ID_IP, IP_Y), (ID_PORT, IP_Y), (ID_CODE, CODE_Y))
                # EDIT 边框（焦点时主色）
                for eid, ey in targets:
                    eh = ctx['edits'][eid]
                    rc = wt.RECT()
                    user32.GetWindowRect(eh, ctypes.byref(rc))
                    pt = wt.POINT(rc.left, rc.top)
                    user32.ScreenToClient(ctx['hwnd'], ctypes.byref(pt))
                    ew = rc.right - rc.left
                    line = C_EDIT_FOCUS if focus == eh else C_EDIT_LINE
                    _round_rect(hdc, pt.x - 2, pt.y - 2, ew + 4, EDIT_H + 4, 6,
                                C_EDIT_BG, line)
                # 唯一的大按钮（后台自动刷新不算「正在连接」，别让按钮自己闪）
                gx, gy, gw, gh = ctx['hotspots'][ID_GO]
                hover = ctx['hover'] == ID_GO
                busy = ctx['busy'] and ctx['busy_row'] is None and not ctx.get('auto_refresh')
                if busy:
                    dots = '.' * (1 + ctx['tick'] % 3)
                    label = ctx['busy_text'] + dots
                    _round_rect(hdc, gx, gy, gw, gh, 10, C_PRIMARY_LT)
                    tw = _text_size(hdc, f_btn, label)[0]
                    _draw_text(hdc, f_btn, gx + (gw - tw) // 2, gy + 11, label, C_PRIMARY)
                else:
                    _round_rect(hdc, gx, gy, gw, gh, 10,
                                C_PRIMARY_DK if ctx['pressed'] == ID_GO
                                else (C_PRIMARY_HOVER if hover else C_PRIMARY))
                    label = '连接设备'
                    tw = _text_size(hdc, f_btn, label)[0]
                    _draw_text(hdc, f_btn, gx + (gw - tw) // 2, gy + 11, label, (255, 255, 255))
    else:
        _draw_text(hdc, f_small, PAD, USB_HINT_Y,
                   '提示：手机上弹出「允许 USB 调试」时勾选「一律允许」，以后即插即用。', C_FAINT)

    # 内联状态区（错误红 / 成功绿 / 进行中主色 / 普通灰）——贴着窗口底部，
    # 这样收起面板时不用为它留一大片固定空隙
    st = ctx['status_text']
    if st:
        kind = ctx['status_kind']
        fg = {'err': C_ERR, 'ok': C_OK, 'busy': C_PRIMARY}.get(kind, C_SUB)
        bg = {'err': C_ERR_LT, 'ok': C_OK_LT, 'busy': C_PRIMARY_LT}.get(kind, C_BG)
        lines = _wrap_text(hdc, f_small, st, W - 2 * PAD - 24)
        bh = 12 + len(lines) * 18
        if ctx.get('pair_qr'):
            floor = IP_Y + QR_SIZE + 12          # 扫码模式没有大按钮，状态行贴着二维码下方
        elif ctx['pair_open']:
            floor = GO_Y + GO_H + 8
        else:
            floor = _pair_link_y(ctx) + 22
        sy = max(floor, H - 12 - bh)
        _round_rect(hdc, PAD, sy, W - 2 * PAD, bh, 8, bg)
        for i, ln in enumerate(lines):
            _draw_text(hdc, f_small, PAD + 12, sy + 7 + i * 18, ln, fg)

    # 「连接成功」toast：列表中央的浅绿圆角条、绿色小字，1 秒后窗口自动关闭
    if ctx.get('toast'):
        tw, th = _text_size(hdc, f_small, ctx['toast'])
        bw, bh = tw + 44, th + 18
        bx = (W - bw) // 2
        by = LIST_Y + (LIST_H - bh) // 2
        _round_rect(hdc, bx, by, bw, bh, 10, C_OK_LT, C_OK)
        _draw_text(hdc, f_small, bx + 22, by + 9, ctx['toast'], C_OK)


def _wrap_text(hdc, font, text, max_w):
    """按像素宽度折行（中文逐字断行即可）。"""
    lines = []
    for para in text.split('\n'):
        cur = ''
        for ch in para:
            if _text_size(hdc, font, cur + ch)[0] > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines or ['']


def _set_tab(ctx, tab):
    ctx['tab'] = tab
    if tab != 'wifi' and ctx.get('pair_open'):
        _set_pair_open(ctx, '')     # 配对面板只属于无线页，切走就收起
    _rebuild_hotspots(ctx)
    _invalidate(ctx)


def _start_job(ctx, kind, payload, row=None, busy_text='正在连接'):
    if ctx['closing'] or ctx.get('dead'):
        return
    if kind == 'qr' and not ctx.get('pair_qr'):
        return                      # 已经切回配对码/收起了：这个等码任务没意义
    if ctx['busy']:
        if kind == 'refresh':
            return
        # 正在刷新设备列表时用户点了「连接设备」：别把这次点击丢掉，等刷新结束再跑
        ctx['queued'] = (kind, payload, row, busy_text)
        _status(ctx, busy_text + '…（正在刷新设备）', 'busy')
        return
    ctx['busy'] = True
    ctx['busy_row'] = row
    ctx['busy_text'] = busy_text
    ctx['run_kind'] = kind
    ctx['tick'] = 0
    auto = kind == 'refresh' and payload == 'auto'
    if auto:
        # 自动刷新只悄悄更新列表：不转圈、不改状态行，避免每 3 秒闪一下
        ctx['auto_refresh'] = True
    else:
        user32.SetTimer(ctx['hwnd'], 1, 400, None)
        _status(ctx, busy_text + '…', 'busy')
    threading.Thread(target=_worker_job, args=(ctx, kind, payload), daemon=True).start()


def _peer_ips(ctx):
    """手头所有可能的手机 IP：设备行地址、历史设备、用户已填的 IP（_click 时抄进 ctx）。"""
    ips = []

    def add(ip):
        if ip and ip not in ips:
            ips.append(ip)

    for r in ctx.get('rows') or []:
        add(_ip_only(r.get('addr') or ''))
    for k in ctx.get('known') or []:
        add(_ip_only(k.get('addr') or ''))
    add(_ip_only(ctx.get('typed_ip') or ''))
    return ips


def _enrich_mdns(discovered, browser=None, timeout=1.5):
    """用自带的 mDNS 浏览器给「发现」行补上地址。

    `adb mdns services` 的缓存经常把同一台手机报成 mDNS 串号却没有地址，
    列表里就会冒出两行同一台手机。自己组播问一次连接服务，就能拿到
    每个串号对应的 ip:port，去重才有据可依。
    """
    items = [dict(d) for d in (discovered or [])]
    browser = browser if browser is not None else mdns_browse
    if browser is None:
        return items
    try:
        found = browser.browse('_adb-tls-connect._tcp', timeout=timeout)
    except Exception:
        return items
    known = {d['serial'] for d in items}
    for it in found or []:
        name = (it.get('name') or '').rstrip('.')
        if name.endswith('.local'):
            name = name[:-6]
        addr = '%s:%s' % (it.get('addr') or '', it.get('port') or '')
        if not name or not IP_PORT_RE.match(addr):
            continue
        if name in known:
            for d in items:
                if d['serial'] == name and not d['addr']:
                    d['addr'] = addr
            continue
        items.append({'name': name.split('.')[0], 'serial': name, 'addr': addr})
        known.add(name)
    return items


def _worker_job(ctx, kind, payload):
    """在工作线程里跑 adb，结果放到 ctx 里再 PostMessage 回窗口。"""
    try:
        if kind == 'refresh':
            online = list_devices_detailed(ctx['adb'], ctx['runner'])
            disc = parse_mdns_all(ctx['runner']([ctx['adb'], 'mdns', 'services'])[1])
            # 自己再问一遍 mDNS：给没有地址的 mDNS 串号补上 ip:port，避免同一台手机列两行
            disc = _enrich_mdns(disc)
            rows = merge_devices(online, disc, ctx['known'])
            # 在线设备补营销名（每台一次 shell，超时兜底型号）
            for r in rows:
                if r['state'] == 'online':
                    model = ''
                    for d in online:
                        if d['serial'] == r['serial']:
                            model = d.get('model') or ''
                            break
                    r['name'] = device_display_name(ctx['adb'], r['serial'],
                                                    model, ctx['runner'])
                elif r['state'] != 'online':
                    r['name'] = _remembered_name(r, ctx['known'])
            ctx['job'] = ('refresh', rows, '')
        elif kind == 'pair':
            ip_port, code = payload
            fail_msg = ''
            if not ip_port:            # 只填了配对码：电脑自己去手机广播里找配对端口
                if mdns_browse is None:
                    fail_msg = ('这个安装包缺少「自动找手机」的组件。请在手机上打开'
                                '「使用配对码配对设备」页面，把上面的 IP 和端口填进'
                                '前两个框，再点「连接设备」。')
                else:
                    ip_port = discover_pair_target(_peer_ips(ctx))
                    if ctx['log']:
                        ctx['log']('wizard: 自动发现配对端口 %r' % (ip_port,))
                    if ip_port:
                        # 让用户看到电脑找到了什么。**不回填输入框**：配对端口用过就失效，
                        # 留在框里下次会被当成连接端口用，必然 10061。
                        ctx['busy_text'] = '正在配对（自动找到 %s）' % ip_port
                    else:
                        fail_msg = ('没找到手机。请让手机停在「使用配对码配对设备」'
                                    '那一页（上面有 6 位码和 IP:端口），再点一次'
                                    '「连接设备」。')
            if ctx.get('dead'):        # 用户已经关掉向导：别在后台偷偷配对
                ctx['job'] = None
                return
            if fail_msg:
                ctx['job'] = ('pair', (False, fail_msg, ''), '')
            else:
                ok, msg, serial = pair_then_connect(ctx['adb'], ip_port, code,
                                                    ctx['runner'], ctx['log'])
                ctx['job'] = ('pair', (ok, msg, serial), '')
        elif kind == 'connect':
            ok, msg, serial = connect_only(ctx['adb'], payload, ctx['runner'])
            ctx['job'] = ('connect', (ok, msg, serial), '')
        elif kind == 'qr':
            # 扫码配对：等设备扫我们的码，期间用户可能切回配对码/收起/关窗
            service, password = payload
            ok, msg, serial = qr_pair(
                ctx['adb'], service, password, ctx['runner'], ctx['log'],
                cancel=lambda: bool(ctx.get('qr_cancel') or ctx.get('dead')),
                peers=_peer_ips(ctx))
            if ctx.get('dead') or (not ok and msg == '已取消扫码配对。'):
                # 窗口关了/用户切回配对码：任务只是收尾，别报错也别继续校正串号
                ctx['job'] = ('idle', '', '')
                user32.PostMessageW(ctx['hwnd'], WM_APP_JOB, 0, 0)
                return
            ctx['job'] = ('qr', (ok, msg, serial), '')
        elif kind == 'reconnect':
            ok, msg, serial = reconnect_known(ctx['adb'], payload, ctx['runner'])
            ctx['job'] = ('reconnect', (ok, msg, serial), '')
    except Exception as exc:
        ctx['job'] = (kind, '', str(exc) or exc.__class__.__name__)
    # 连接成功后统一成 adb 此刻真正认的串号（ip:port ↔ mDNS 串号），否则投屏起不来
    job = ctx.get('job')
    if job and job[0] != 'refresh' and isinstance(job[1], tuple) and len(job[1]) > 2:
        ok, msg, serial = job[1][0], job[1][1], job[1][2]
        if ok and serial:
            fixed = resolve_serial(ctx['adb'], serial, ctx['runner'])
            if fixed != serial:
                ctx['job'] = (job[0], (ok, msg, fixed), job[2])
    user32.PostMessageW(ctx['hwnd'], WM_APP_JOB, 0, 0)


def _on_job_done(ctx):
    _apply_job(ctx)
    # 刷新期间被拦下的点击，等这次任务收尾后补跑
    queued = ctx.pop('queued', None)
    if queued and not ctx['closing'] and not ctx.get('dead'):
        _start_job(ctx, *queued)


def _apply_job(ctx):
    job = ctx.get('job')
    if not job:
        return
    ctx['job'] = None
    kind = job[0]
    user32.KillTimer(ctx['hwnd'], 1)
    ctx['busy'] = False
    ctx['busy_row'] = None
    ctx['run_kind'] = ''
    if kind == 'idle':        # 等扫码的任务被取消/窗口已关：只收尾，不报错
        return
    if kind == 'qr' and not ctx.get('pair_qr') and isinstance(job[1], tuple) \
            and not job[1][0]:
        return                # 已经切回配对码：别再往状态行写扫码的结果（成功仍要收尾）
    if job[2]:   # 工作线程抛异常（refresh 也算，不能静默当成 0 台设备）
        if ctx['log']:
            ctx['log']('wizard: %s 异常 %s' % (kind, job[2]))
        _status(ctx, friendly_error(job[2], '连接'), 'err')
        _invalidate(ctx)
        return
    if kind == 'refresh':
        rows = job[1] or []
        ctx['rows'] = rows
        ctx['scroll'] = min(ctx.get('scroll') or 0, _max_scroll(ctx))
        _rebuild_hotspots(ctx)
        auto = ctx.pop('auto_refresh', False)
        # 一台能操作的设备都没有也什么都不做：配对面板要用户自己点「手动配对」才出现
        # （用户要求：打开窗口时只看到设备列表，别塞一个配对表单过来）。
        # 列表本身就是答案，不用再报「找到 N 台设备」（用户嫌啰嗦）。
        # 只有用户自己点刷新才清空状态行：自动刷新（每 3 秒）绝不能把
        # 用户刚看到的提示清掉——之前「点手动配对后提示一闪就没」就是这么来的。
        if not auto:
            _status(ctx, '', 'info')
        if ctx['log'] and not auto:
            ctx['log']('wizard: devices=%s' % ([r['serial'] for r in rows],))
        _invalidate(ctx)
        return
    val = job[1] if isinstance(job[1], tuple) else (False, str(job[1]), '')
    ok, msg, serial = val[0], val[1], val[2]
    if ctx['log']:
        ctx['log']('wizard: %s -> ok=%s msg=%s' % (kind, ok, msg))
    if ok:
        _status(ctx, '已连接，正在开始投屏…', 'ok')
        _finish(ctx, serial)
    else:
        _status(ctx, msg, 'err')
        _invalidate(ctx)


def _notify_close(ctx):
    """窗口没了但没连上（用户点 X）：通知外面把「向导已开」的状态清掉。"""
    if ctx.get('closed'):
        return
    ctx['closed'] = True
    if ctx.get('on_close'):
        try:
            ctx['on_close']()
        except Exception as exc:
            if ctx['log']:
                ctx['log']('wizard: on_close 失败 %s' % exc)


def _finish(ctx, serial):
    """连接成功：先显示「连接成功」toast，1 秒后窗口自动关闭并通知外面开始投屏。"""
    if ctx['closing']:
        return
    ctx['closing'] = True
    ctx['toast'] = '连接成功'
    ctx['pending_serial'] = serial
    user32.SetTimer(ctx['hwnd'], 2, SUCCESS_CLOSE_MS, None)
    _invalidate(ctx)


def _complete(ctx, serial):
    hwnd = ctx['hwnd']
    ctx['closed'] = True
    name = ''
    addr = ''
    for r in ctx['rows']:
        if r['serial'] == serial or r['addr'] == serial:
            name = r['name']
            addr = r['addr'] or ''
            break
    if ctx['log']:
        ctx['log']('wizard: done serial=%s name=%s' % (serial, name))
    user32.DestroyWindow(hwnd)
    if ctx.get('on_done'):
        # 按签名判参数个数，不用 except TypeError 兜底——
        # 否则 on_done 函数体内部抛 TypeError 会被误判再调一次（投屏双启动）
        try:
            n = len(inspect.signature(ctx['on_done']).parameters)
        except (TypeError, ValueError):
            n = 2
        try:
            if n >= 3:
                ctx['on_done'](serial, name, addr)
            elif n == 2:
                ctx['on_done'](serial, name)
            else:
                ctx['on_done'](serial)
        except Exception as exc:
            if ctx['log']:
                ctx['log']('wizard: on_done 失败 %s' % exc)


def _click(ctx, hid):
    if ctx['closing']:   # toast 期间不再响应点击，避免重复连接
        return
    if hid == ID_TAB_WIFI:
        _set_tab(ctx, 'wifi')
        return
    if hid == ID_TAB_USB:
        _set_tab(ctx, 'usb')
        return
    if hid == ID_REFRESH:
        _start_job(ctx, 'refresh', None, busy_text='正在查找设备')
        return
    if hid == ID_MANUAL:
        _set_pair_open(ctx, 'manual')      # 提示语由 _set_pair_open 按模式给
        user32.SetFocus(ctx['edits'][ID_IP])
        return
    if hid == ID_PAIR_CLOSE:
        _set_pair_open(ctx, '')
        _status(ctx, '', 'info')
        return
    if hid == ID_MODE_CODE:
        _set_pair_kind(ctx, False)
        return
    if hid == ID_MODE_QR:
        _set_pair_kind(ctx, True)
        return
    if hid == ID_QR_REFRESH:
        _refresh_qr(ctx)
        return
    if hid == ID_GO:
        if not ctx['pair_open']:   # 面板收着时这个按钮不存在
            return
        ctx['typed_ip'] = _text(ctx['edits'][ID_IP])   # 抄给 worker，别让它跨线程读 EDIT
        kind, payload, err = decide_action(ctx['typed_ip'],
                                           _text(ctx['edits'][ID_PORT]),
                                           _text(ctx['edits'][ID_CODE]))
        if not kind:
            _status(ctx, err, 'err')
            return
        if kind == 'pair':
            _start_job(ctx, 'pair', payload, busy_text='正在配对并连接')
        else:
            _start_job(ctx, 'connect', payload, busy_text='正在连接')
        return
    if hid >= ID_ROW_BASE:
        i = hid - ID_ROW_BASE
        rows = ctx['visible_rows']
        if i >= len(rows):
            return
        r = rows[i]
        if r['state'] == 'online':
            # 已连接的设备：点了也不做任何事（不重复连接，也就不会开出第二个投屏窗口）
            _status(ctx, '「%s」已经连上了，不用再点。' % r['name'], 'info')
            return
        # 没连上的设备一律先配对：填 6 位配对码再点「连接设备」
        _prefill_pair(ctx, r)
        return


def _wndproc(hwnd, msg, wparam, lparam):
    ctx = _WINDOWS.get(hwnd)
    if not ctx:
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        # 双缓冲：整窗先画进离屏位图，再 BitBlt 一次上屏。
        # 直画屏幕 DC 会「先刷背景再补文字」，悬停重绘时肉眼可见闪烁跳动。
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, WIN_W, WIN_H)
        old = gdi32.SelectObject(mem, bmp)
        try:
            _paint(ctx, mem)
            gdi32.BitBlt(hdc, 0, 0, WIN_W, WIN_H, mem, 0, 0, 0x00CC0020)  # SRCCOPY
        except Exception as exc:
            # 绘制异常绝不能让 EndPaint 被跳过，否则 WM_PAINT 无限风暴
            if ctx['log']:
                ctx['log']('wizard: 绘制异常 %s' % exc)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_ERASEBKGND:
        return 1   # 全部自绘，禁止系统擦背景（防闪烁）
    if msg == WM_CTLCOLORSTATIC or msg == WM_CTLCOLOREDIT:
        hdc = wt.HDC(wparam)
        gdi32.SetBkColor(hdc, _rgb(C_EDIT_BG))
        gdi32.SetTextColor(hdc, _rgb(C_INK))
        return ctypes.c_void_p(ctx['edit_br']).value
    if msg == WM_SETFOCUS or msg == WM_KILLFOCUS:
        _invalidate(ctx)   # 焦点变化时 placeholder 要显示/隐藏
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_SETCURSOR:
        if (lparam & 0xFFFF) == HTCLIENT:
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.ScreenToClient(hwnd, ctypes.byref(pt))
            if _hit(ctx, pt.x, pt.y):
                user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(32649)))  # IDC_HAND
                return 1
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_MOUSEMOVE:
        x, y = _xy(lparam)
        if not ctx['tracking']:
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
            user32.TrackMouseEvent(ctypes.byref(tme))
            ctx['tracking'] = True
        hid = _hit(ctx, x, y)
        if hid != ctx['hover']:
            ctx['hover'] = hid
            _invalidate(ctx)
        return 0
    if msg == WM_MOUSELEAVE:
        ctx['tracking'] = False
        if ctx['hover']:
            ctx['hover'] = 0
            _invalidate(ctx)
        return 0
    if msg == WM_MOUSEWHEEL:
        # 列表能显示的行放不下时，滚轮上下翻（lParam 是屏幕坐标，先换算成客户区）
        pt = wt.POINT(*_xy(lparam))
        user32.ScreenToClient(hwnd, ctypes.byref(pt))
        if PAD <= pt.x <= WIN_W - PAD and LIST_Y <= pt.y <= LIST_Y + _list_h(ctx):
            notches = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            step = int(round(notches / 120.0)) or (1 if notches > 0 else -1)
            _scroll_by(ctx, -step)
        return 0
    if msg == WM_LBUTTONDOWN:
        x, y = _xy(lparam)
        hid = _hit(ctx, x, y)
        if hid:
            ctx['pressed'] = hid
            user32.SetCapture(hwnd)   # 拖出窗口再松开也能收到 UP
            _invalidate(ctx)
        return 0
    if msg == WM_LBUTTONUP:
        x, y = _xy(lparam)
        user32.ReleaseCapture()
        hid = _hit(ctx, x, y)
        pressed = ctx['pressed']
        ctx['pressed'] = 0
        if hid and hid == pressed:
            _click(ctx, hid)
        _invalidate(ctx)
        return 0
    if msg == WM_COMMAND:
        cid = wparam & 0xFFFF
        code = (wparam >> 16) & 0xFFFF
        if cid in (ID_GO, IDOK) and code == BN_CLICKED:
            # IDOK：IsDialogMessage 把 EDIT 里的回车转成默认按钮点击
            _click(ctx, ID_GO)
            return 0
        if code == EN_CHANGE and cid in (ID_IP, ID_PORT, ID_CODE):
            _invalidate(ctx)   # 输入时重画焦点框
            return 0
        return 0
    if msg == WM_KEYDOWN:
        if wparam == VK_RETURN:
            if ctx['tab'] == 'wifi':
                _click(ctx, ID_GO)
            return 0
        if wparam == VK_ESCAPE:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return 0
        if wparam == VK_TAB:
            # 在三个输入框之间循环焦点：IP → 端口 → 配对码
            order = [ctx['edits'][ID_IP], ctx['edits'][ID_PORT],
                     ctx['edits'][ID_CODE]]
            if ctx['tab'] == 'wifi':
                cur = user32.GetFocus()
                nxt = order[(order.index(cur) + 1) % 3] if cur in order else order[0]
                user32.SetFocus(nxt)
            return 0
        return 0
    if msg == 0x0113:   # WM_TIMER
        if wparam == 2:   # 「连接成功」toast 满 1 秒：关窗、通知开始投屏
            user32.KillTimer(hwnd, 2)
            _complete(ctx, ctx.get('pending_serial', ''))
            return 0
        if wparam == AUTO_REFRESH_TIMER:
            # 每 3 秒悄悄刷新列表：手机重开无线调试后，几秒内自己变回「新发现/已连接」
            if not ctx['busy'] and not ctx['closing'] and not ctx.get('dead'):
                _start_job(ctx, 'refresh', 'auto')
            return 0
        ctx['tick'] += 1
        if ctx['busy']:
            _invalidate(ctx)
        return 0
    if msg == WM_APP_JOB:
        _on_job_done(ctx)
        return 0
    if msg == WM_CLOSE:
        if ctx['log']:
            ctx['log']('wizard: 用户关闭')
        _notify_close(ctx)
        user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        ctx['dead'] = True         # 工作线程看到就别再继续（关窗后不该偷偷配对）
        _notify_close(ctx)
        for f in ('f_title', 'f_body', 'f_small', 'f_btn', 'f_bold'):
            if ctx.get(f):
                gdi32.DeleteObject(ctx[f])
        if ctx.get('edit_br'):
            gdi32.DeleteObject(ctx['edit_br'])
        if ctx.get('hicon'):
            user32.DestroyIcon(ctx['hicon'])
        for eh in ctx['edits'].values():
            prev = _EDIT_OLD.pop(eh, None)
            if prev:
                user32.SetWindowLongPtrW(eh, -4, prev)   # 还原原 wndproc 再销毁
        _WINDOWS.pop(hwnd, None)
        user32.PostQuitMessage(0)   # 退出本线程消息循环，否则向导线程永久挂起
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _edit_proc(hwnd, msg, wparam, lparam):
    """EDIT 子类化：原生绘制完成后叠画 placeholder。

    父窗口带 WS_CLIPCHILDREN，自绘灰字会被 EDIT 区域裁掉，
    所以必须画在 EDIT 自己的 DC 上。
    WM_PAINT 后更新区已空，BeginPaint 会裁掉一切，用 GetDC 叠画；
    PrintWindow 走 WM_PRINTCLIENT，用 wParam 传入的 DC 叠画。
    """
    old = _EDIT_OLD.get(hwnd)
    if not old:
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    r = user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)
    if msg in (WM_SETFOCUS, WM_KILLFOCUS):
        user32.InvalidateRect(hwnd, None, 0)   # 聚焦/失焦时 placeholder 要隐藏/显示
        return r
    if msg == WM_PAINT or msg == WM_PRINTCLIENT:
        ctx = _WINDOWS.get(user32.GetParent(hwnd))
        cue = ctx['cues'].get(user32.GetDlgCtrlID(hwnd), '') if ctx else ''
        if cue and not _text(hwnd).strip() and user32.GetFocus() != hwnd:
            if msg == WM_PAINT:
                hdc = user32.GetDC(hwnd)
                try:
                    _draw_cue(hdc, ctx, hwnd, cue)
                finally:
                    user32.ReleaseDC(hwnd, hdc)
            else:
                _draw_cue(wt.HDC(wparam), ctx, hwnd, cue)
    return r


def _draw_cue(hdc, ctx, eh, cue):
    # 框高 20 时原生文字 ink 中心=10，placeholder 用同一公式（得 y=0）对齐；x=7 与 margins 后文字左缘一致
    rc = wt.RECT()
    user32.GetClientRect(eh, ctypes.byref(rc))
    th = _text_size(hdc, ctx['f_body'], '0')[1]
    _draw_text(hdc, ctx['f_body'], 7, max(0, (rc.bottom - th) // 2), cue, C_CUE)


_EDIT_PROC_REF = _WNDPROC(_edit_proc)


def _make_edit(ctx, text, x, y, w, h, cid, number=False, cue=''):
    style = WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL | WS_TABSTOP
    if number:
        style |= ES_NUMBER
    eh = user32.CreateWindowExW(0, 'EDIT', text, style, x, y, w, h,
                                ctx['hwnd'], cid,
                                kernel32.GetModuleHandleW(None), None)
    user32.SendMessageW(eh, WM_SETFONT, ctx['f_body'], 1)
    # 左右留白 6（文字左缘 rel 7），与 placeholder 的 x=7 对齐
    user32.SendMessageW(eh, EM_SETMARGINS, 1 | 2, 6 | (6 << 16))
    if cue:
        # placeholder 由 EDIT 子类化自绘（空且未聚焦时灰字）；原生 cue banner 需 comctl32 v6，不用
        ctx['cues'][cid] = cue
        prev = user32.SetWindowLongPtrW(eh, -4, ctypes.cast(_EDIT_PROC_REF, ctypes.c_void_p))
        _EDIT_OLD[eh] = prev
    return eh


def show_wizard(adb, on_done, on_log=None, on_close=None, known=None, runner=run_cmd,
                icon_path=None):
    """在独立线程里弹出自绘向导；on_done(serial, name) 在向导线程里被调用。

    known: CFG['known_devices'] 历史设备列表（可为 None）。
    runner: adb 命令执行器（测试可注入）。
    icon_path: 窗口图标 .ico 路径（主程序传 app.ico；None 用系统默认图标）。
    """
    def _log(msg):
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    def _worker():
        try:
            _build_and_run(adb, on_done, on_log, on_close, known, runner, icon_path)
        except Exception as exc:                 # 线程里没人接异常，一定要写日志
            import traceback
            _log('wizard: 异常 %s' % exc)
            _log('wizard: ' + traceback.format_exc().replace('\n', ' | '))

    def _build_and_run(adb, on_done, on_log, on_close, known, runner, icon_path):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = _WNDPROC(_wndproc)
        _PROC_REFS.append(wc.lpfnWndProc)
        wc.hInstance = hinst
        wc.lpszClassName = 'MoyuConnectWizard'
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
        wc.hbrBackground = None
        # 窗口图标 = 魔芋应用图标（标题栏/任务栏/Alt-Tab 都用它）
        hicon = None
        if icon_path:
            hicon = user32.LoadImageW(None, icon_path, 1, 0, 0,
                                      0x0010 | 0x0040)  # IMAGE_ICON|LOAD_FROMFILE|LOAD_DEFAULTSIZE
            if not hicon:
                hicon = user32.LoadImageW(None, icon_path, 1, 16, 16, 0x0010)
        wc.hIcon = hicon
        try:
            user32.RegisterClassW(ctypes.byref(wc))
        except Exception:
            pass
        # 居中于工作区
        wa = wt.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
        wx = wa.left + max(0, ((wa.right - wa.left) - WIN_W) // 2)
        wy = wa.top + max(0, ((wa.bottom - wa.top) - WIN_H_CLOSED) // 2)
        style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN
        hwnd = user32.CreateWindowExW(0, 'MoyuConnectWizard', '魔芋 · 连接设备',
                                      style, wx, wy, WIN_W, WIN_H_CLOSED, None, None, hinst, None)
        if not hwnd:
            if on_log:
                on_log('wizard: 创建窗口失败 err=%s' % ctypes.get_last_error())
            return
        if hicon:
            # 类图标只对新建窗口生效；显式再发一次，确保标题栏与任务栏都换掉
            user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)   # ICON_SMALL
            user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)   # ICON_BIG
        # 客户区精确到 WIN_W x WIN_H_CLOSED（CreateWindow 的尺寸含边框）
        _fit_client(hwnd, WIN_W, WIN_H_CLOSED)
        ctx = {'hwnd': hwnd, 'adb': adb, 'on_done': on_done, 'log': on_log,
               'on_close': on_close, 'closed': False, 'job': None, 'busy': False,
               'busy_row': None, 'busy_text': '', 'tick': 0, 'hicon': hicon,
               'known': known or [], 'runner': runner,
               'tab': 'wifi', 'rows': [], 'visible_rows': [],
               'hotspots': {}, 'hover': 0, 'pressed': 0, 'tracking': False,
               'status_text': '', 'status_kind': 'info', 'edits': {}, 'cues': {},
               'toast': '', 'closing': False, 'dead': False, 'typed_ip': '',
               'queued': None, 'auto_refresh': False, 'run_kind': '',
               'pair_open': False, 'pair_mode': '', 'pair_label': '',
               'pair_name': '',
               'pair_qr': False, 'qr_creds': (), 'qr_rows': [], 'qr_cancel': False,
               'scroll': 0}
        ctx['f_title'] = _font_create(19, 600)
        ctx['f_body'] = _font_create(14, 400)
        ctx['f_small'] = _font_create(12, 400)
        ctx['f_btn'] = _font_create(13, 600)
        ctx['f_bold'] = _font_create(14, 700)
        ctx['edit_br'] = gdi32.CreateSolidBrush(_rgb(C_EDIT_BG))
        _WINDOWS[hwnd] = ctx
        # 输入框（原生 EDIT：输入法/复制粘贴/光标全都白拿）
        # IP 与端口同一行、中间画冒号，用户不用输「:」
        ctx['edits'][ID_IP] = _make_edit(ctx, '', EDIT_X, IP_Y, IP_W, EDIT_H, ID_IP,
                                         cue='例如 192.168.1.5')
        ctx['edits'][ID_PORT] = _make_edit(ctx, '', EDIT_X + IP_W + 2 * COLON_GAP,
                                           IP_Y, PORT_W, EDIT_H, ID_PORT,
                                           number=True, cue='4-5 位端口号')
        ctx['edits'][ID_CODE] = _make_edit(ctx, '', EDIT_X, CODE_Y, 120, EDIT_H, ID_CODE,
                                           number=True, cue='6 位配对码')
        _set_pair_open(ctx, '')     # 平时收起：配对面板只在点「配对」/「手动配对」时出来
        _rebuild_hotspots(ctx)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetTimer(hwnd, AUTO_REFRESH_TIMER, AUTO_REFRESH_MS, None)
        _start_job(ctx, 'refresh', None, busy_text='正在查找设备')
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            # IsDialogMessage 负责 Tab 焦点导航，并把 EDIT 里的回车转成
            # 默认按钮（ID_GO）的 WM_COMMAND；Esc/自绘按钮仍走 _wndproc。
            if user32.IsDialogMessageW(hwnd, ctypes.byref(msg)):
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    t = threading.Thread(target=_worker, name='connect-wizard', daemon=True)
    t.start()
    return t