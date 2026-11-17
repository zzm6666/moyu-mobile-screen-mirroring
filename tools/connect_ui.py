# -*- coding: utf-8 -*-
"""连接手机向导：逻辑层 + 自绘 UI（纯 ctypes Win32，无第三方依赖）。

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
  首次配对只有 IP:端口 + 配对码 + 一个「连接手机」大按钮，
  点击后配对→找连接端口→连接全自动，用户无感。
  窗口自绘（GDI 圆角/配色/微软雅黑），输入框仍是原生 EDIT 保证输入法可用。
"""
import ctypes
import ctypes.wintypes as wt
import inspect
import json
import os
import re
import subprocess
import threading
import time

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
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_CTLCOLORSTATIC = 0x0138
WM_CTLCOLOREDIT = 0x0133
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_MOUSELEAVE = 0x02A3
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
                       '「使用配对码配对设备」，用新弹出的端口和配对码再试一次。'),
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
    """只做 adb connect；返回 (是否成功, 通俗提示, 串号)。"""
    if not normalize_ip_port(ip_port):
        return False, '请填写「IP:端口」，例如 192.168.1.5:41001', ''
    rc, out = runner([adb, 'connect', ip_port])
    low = (out or '').lower()
    if 'connected to' in low or 'already connected' in low:
        return True, '连接成功', ip_port
    return False, friendly_error(out, '连接'), ''


def list_online(adb, runner=run_cmd):
    """当前在线设备（状态 device 才算）。"""
    rc, out = runner([adb, 'devices'])
    devs = []
    for line in (out or '').splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == 'device':
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
        m = re.match(r'^adb-[0-9a-f]+(?:-(.+?))?\._adb', serial)
        if m and m.group(1):
            return m.group(1)
        return '无线设备'   # 无中间段的 mDNS 串号也绝不原样显示
    return serial or '未知设备'


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

    def _identity_key(addr, serial):
        # mDNS 串号与 ip:port 指向同一台手机时用 addr 去重
        return addr or serial

    for d in online:
        serial = d['serial']
        addr = serial if IP_PORT_RE.match(serial) else ''
        rows.append({'serial': serial, 'name': d.get('model') or serial,
                     'addr': addr, 'kind': classify_device(serial), 'state': 'online'})
        seen_serial.add(serial)
        if addr:
            seen_addr.add(addr)
    for d in discovered:
        if d['serial'] in seen_serial:
            # 同一台手机已在线（mDNS 串号）：把它的 ip:port 记进 seen_addr，
            # 否则 known 里的 ip:port 历史行会重复出现（在线+离线两行）
            if d['addr']:
                seen_addr.add(d['addr'])
            continue
        if d['addr'] in seen_addr:
            continue
        rows.append({'serial': d['addr'], 'name': d['name'], 'addr': d['addr'],
                     'kind': 'wifi', 'state': 'found'})
        seen_serial.add(d['serial'])
        seen_addr.add(d['addr'])
    for k in known or []:
        serial = k.get('serial') or ''
        addr = k.get('addr') or (serial if IP_PORT_RE.match(serial) else '')
        if serial in seen_serial or (addr and addr in seen_addr):
            continue
        # 同一台手机换了端口：IP 相同就视为同一台，不再重复列历史行
        ip = _ip_of(addr)
        if ip and any(_ip_of(r['addr']) == ip for r in rows if r['addr']):
            continue
        rows.append({'serial': serial, 'name': k.get('name') or serial,
                     'addr': addr, 'kind': classify_device(serial), 'state': 'offline'})
        seen_serial.add(serial)
        if addr:
            seen_addr.add(addr)
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
    # 3) mDNS 找新端口（优先同 IP）
    ip = _ip_of(addr)
    found = parse_mdns_services(runner([adb, 'mdns', 'services'])[1], ip)
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


def pair_then_connect(adb, ip_port, code, runner=run_cmd):
    """一键「连接手机」：配对 + 自动找到真正的「连接端口」再连，用户无感。

    手机无线调试有两个端口：配对弹窗里的**配对端口**和主页面上「IP 地址和端口」的
    **连接端口**，两者通常不同——配对成功后不能拿配对端口去 connect。
    这里按三步找连接方式：
      1. 配对成功后先看 `adb devices`（mDNS 常常直接出现可用设备）；
      2. 不行再查 `adb mdns services` 拿该 IP 的连接端口，然后 connect；
      3. 都不行就给出通俗提示（不再让用户手填连接端口——自动重试 mDNS 几轮）。
    """
    ok, msg = pair_only(adb, ip_port, code, runner)
    if not ok:
        return False, msg, ''
    ip = _ip_of(ip_port)
    # mDNS 广播有延迟：多等几轮，尽量不让用户做任何额外操作
    for attempt in range(6):
        devs = list_online(adb, runner)
        if devs:
            return True, '配对并连接成功', devs[0]
        serial = parse_mdns_services(runner([adb, 'mdns', 'services'])[1], ip)
        if serial:
            ok2, msg2, s2 = connect_only(adb, serial, runner)
            if ok2:
                return True, '配对并连接成功', s2
        time.sleep(1.0)
    return False, ('配对成功了，但电脑暂时没找到手机的连接端口。请保持手机「无线调试」'
                   '页面亮着，稍等几秒后再点一次「连接手机」。'), ''


def remember_device(cfg, serial, name, addr='', now=None):
    """把成功连过的设备写进 cfg['known_devices']（按身份去重，最多 8 台，新在前）。

    返回更新后的列表（原地改 cfg）。now 可注入便于测试。
    """
    known = cfg.get('known_devices')
    if not isinstance(known, list):
        known = []
    addr = addr or (serial if IP_PORT_RE.match(serial or '') else '')
    ip = _ip_of(addr)
    entry = {'serial': serial, 'name': name or serial, 'addr': addr,
             'last_seen': int(now if now is not None else time.time())}
    rest = []
    for k in known:
        if k.get('serial') == serial:
            continue
        if ip and _ip_of(k.get('addr') or '') == ip:
            continue   # 同一台手机换了端口：旧记录作废
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
C_PRIMARY_LT = (230, 246, 243)  # 主色 浅底（选中 tab 底/徽标底）
C_ACCENT = (217, 119, 6)        # 琥珀（新发现徽标/提示图标）
C_ACCENT_LT = (253, 240, 222)
C_OK = (22, 163, 74)            # 在线
C_OK_LT = (226, 246, 233)
C_ERR = (200, 44, 44)           # 错误红
C_ERR_LT = (252, 235, 235)
C_OFF = (156, 163, 173)         # 离线灰
C_OFF_LT = (238, 238, 236)
C_HOVER = (240, 239, 235)       # 行 hover
C_EDIT_BG = (255, 255, 255)
C_EDIT_LINE = (203, 201, 195)
C_EDIT_FOCUS = (23, 152, 140)
C_CUE = (163, 161, 155)           # 输入框 placeholder 灰字


def _rgb(c):
    return c[0] | (c[1] << 8) | (c[2] << 16)


# 布局常量（客户区坐标，DPI 未缩放时按 96dpi 设计；窗口固定尺寸）
WIN_W, WIN_H = 520, 560
PAD = 20
TAB_Y, TAB_H = 56, 34
LIST_Y = TAB_Y + TAB_H + 14
ROW_H = 52
MAX_ROWS = 4
LIST_H = ROW_H * MAX_ROWS
SEC_Y = LIST_Y + LIST_H + 16          # 「首次配对」区标题
IP_Y = SEC_Y + 30
CODE_Y = IP_Y + 40
GO_Y = CODE_Y + 44
SUCCESS_CLOSE_MS = 1000         # 「连接成功」toast 停留时长，之后窗口自动关闭
GO_H = 40
STATUS_Y = GO_Y + GO_H + 12
USB_HINT_Y = LIST_Y + LIST_H + 16
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
    """客户区坐标 → 可点元素 id；没有则 0。"""
    for hid, (rx, ry, rw, rh) in ctx['hotspots'].items():
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return hid
    # 在线设备没有按钮：整行可点，点一下直接开始投屏
    if PAD <= x < WIN_W - PAD:
        for i, r in enumerate(ctx['visible_rows']):
            ry = LIST_Y + i * ROW_H
            if r['state'] == 'online' and ry <= y < ry + ROW_H:
                return ID_ROW_BASE + i
    return 0


def _rebuild_hotspots(ctx):
    hs = {}
    hs[ID_TAB_WIFI] = (PAD, TAB_Y, 108, TAB_H)
    hs[ID_TAB_USB] = (PAD + 116, TAB_Y, 108, TAB_H)
    rows = ctx['rows']
    if ctx['tab'] == 'usb':
        rows = [r for r in rows if r['kind'] == 'usb']
    else:
        rows = [r for r in rows if r['kind'] == 'wifi']
    ctx['visible_rows'] = rows[:MAX_ROWS]
    for i, r in enumerate(ctx['visible_rows']):
        y = LIST_Y + i * ROW_H
        if r['state'] != 'online':   # 已连接的设备不再显示「连接」按钮
            hs[ID_ROW_BASE + i] = (WIN_W - PAD - 76, y + 10, 64, 32)
    hs[ID_REFRESH] = (WIN_W - PAD - 64, TAB_Y + 4, 64, 26)
    if ctx['tab'] == 'wifi':
        hs[ID_GO] = (PAD, GO_Y, WIN_W - 2 * PAD, GO_H)
    ctx['hotspots'] = hs


def _paint(ctx, hdc):
    W, H = WIN_W, WIN_H
    _fill_rect(hdc, 0, 0, W, H, C_BG)
    f_title = ctx['f_title']
    f_body = ctx['f_body']
    f_small = ctx['f_small']
    f_btn = ctx['f_btn']

    # 标题区
    _draw_text(hdc, f_title, PAD, 16, '连接手机', C_INK)
    _draw_text(hdc, f_small, PAD + 92, 24, '连上后自动开始投屏', C_FAINT)

    # Tab 栏（分段式）
    for tid, label in ((ID_TAB_WIFI, '无线连接'), (ID_TAB_USB, 'USB 连接')):
        x, y, w, h = ctx['hotspots'][tid]
        active = (tid == ID_TAB_WIFI and ctx['tab'] == 'wifi') or \
                 (tid == ID_TAB_USB and ctx['tab'] == 'usb')
        hover = ctx['hover'] == tid
        if active:
            _round_rect(hdc, x, y, w, h, 10, C_PRIMARY)
            _draw_text(hdc, f_btn, x + (w - _text_size(hdc, f_btn, label)[0]) // 2,
                       y + 8, label, (255, 255, 255))
        else:
            _round_rect(hdc, x, y, w, h, 10, C_HOVER if hover else C_SURFACE,
                        C_LINE)
            _draw_text(hdc, f_btn, x + (w - _text_size(hdc, f_btn, label)[0]) // 2,
                       y + 8, label, C_SUB if not hover else C_INK)

    # 刷新按钮（右上角小按钮，纯文字）
    rx, ry, rw, rh = ctx['hotspots'][ID_REFRESH]
    hover = ctx['hover'] == ID_REFRESH
    fg = C_PRIMARY if hover else C_SUB
    _round_rect(hdc, rx, ry, rw, rh, 8, C_PRIMARY_LT if hover else C_SURFACE, C_LINE)
    tw = _text_size(hdc, f_small, '刷新')[0]
    _draw_text(hdc, f_small, rx + (rw - tw) // 2, ry + 5, '刷新', fg)

    # 设备列表卡片
    rows = ctx['visible_rows']
    _round_rect(hdc, PAD, LIST_Y - 6, W - 2 * PAD, LIST_H + 12, 12, C_SURFACE, C_LINE)
    if not rows:
        if ctx['tab'] == 'wifi':
            msg1 = '还没有无线设备'
            msg2 = '手机开启「无线调试」后会自动出现在这里；'
            msg3 = '第一次使用请在下方完成配对。'
        else:
            msg1 = '没有检测到 USB 设备'
            msg2 = '用数据线连接手机，并在手机上'
            msg3 = '允许「USB 调试」，然后点右上角「刷新」。'
        cx = W // 2
        for i, (m, f, c) in enumerate(((msg1, f_body, C_SUB), (msg2, f_small, C_FAINT),
                                       (msg3, f_small, C_FAINT))):
            tw = _text_size(hdc, f, m)[0]
            _draw_text(hdc, f, cx - tw // 2, LIST_Y + 52 + i * 24, m, c)
    for i, r in enumerate(rows):
        y = LIST_Y + i * ROW_H
        # hover 只作用于「连接」按钮本身，设备行不做整行高亮
        if i:
            _hline(hdc, PAD + 14, y, W - 2 * PAD - 28, C_LINE)
        # 名称（营销名/型号，绝不显示 mDNS 串号）；过长先截断，给后面的徽标留位
        name = r['name']
        dot_c = {'online': C_OK, 'found': C_ACCENT, 'offline': C_OFF}[r['state']]
        label, fg, bg = {'online': ('已连接', C_OK, C_OK_LT),
                         'found': ('新发现', C_ACCENT, C_ACCENT_LT),
                         'offline': ('离线', C_OFF, C_OFF_LT)}[r['state']]
        bw = _text_size(hdc, f_small, label)[0] + 14
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
        _draw_text(hdc, f_body, nx, name_y, name,
                   C_INK if r['state'] != 'offline' else C_SUB)
        # 状态徽标紧跟设备名同一行，与名称文字垂直居中对齐
        nw = _text_size(hdc, f_body, name)[0]
        bh = _text_size(hdc, f_small, label)[1] + 6
        _badge(hdc, f_small, nx + nw + 8, name_y + (20 - bh) // 2, label, fg, bg)
        if sub:
            _draw_text(hdc, f_small, nx, sub_y, sub, C_FAINT)
        # 行尾「连接」按钮：已连接（在线）的设备不显示，整行可点直接投屏
        if r['state'] == 'online':
            continue
        bx, by, bw, bh = ctx['hotspots'][ID_ROW_BASE + i]
        hover = ctx['hover'] == ID_ROW_BASE + i
        busy = ctx['busy'] and ctx['busy_row'] == i
        if busy:
            _round_rect(hdc, bx, by, bw, bh, 8, C_PRIMARY_LT)
            _draw_text(hdc, f_small, bx + 10, by + 8, '连接中', C_PRIMARY)
        elif r['state'] == 'offline':
            # hover 参照刷新按钮：浅绿底 + 主色文字（原来 hover 无任何变化）
            _round_rect(hdc, bx, by, bw, bh, 8,
                        C_PRIMARY_LT if hover else C_SURFACE, C_LINE)
            _draw_text(hdc, f_btn, bx + 16, by + 7, '连接',
                       C_PRIMARY if hover else C_SUB)
        else:
            # hover 参照刷新按钮：浅绿底 + 主色文字（深绿→更深绿肉眼几乎看不出）
            _round_rect(hdc, bx, by, bw, bh, 8,
                        C_PRIMARY_LT if hover else C_PRIMARY)
            _draw_text(hdc, f_btn, bx + 16, by + 7, '连接',
                       C_PRIMARY if hover else (255, 255, 255))

    if ctx['tab'] == 'wifi':
        # 首次配对区
        _draw_text(hdc, f_body, PAD, SEC_Y, '首次配对', C_INK)
        _draw_text(hdc, f_small, PAD + 76, SEC_Y + 3,
                   '手机：设置 → 开发者选项 → 无线调试 → 使用配对码配对', C_FAINT)
        _draw_text(hdc, f_small, PAD, IP_Y + 2, 'IP 端口', C_SUB)
        _draw_text(hdc, f_small, PAD, CODE_Y + 2, '配对码', C_SUB)
        # IP 与端口之间的冒号（两个输入框同一行，用户不用输「:」）
        # 冒号在间隙里水平居中、与框内文字同一垂直中线
        cw, ch = _text_size(hdc, f_body, ':')
        colon_x = EDIT_X + IP_W + (2 * COLON_GAP - cw) // 2
        _draw_text(hdc, f_body, colon_x, IP_Y + (EDIT_H - ch) // 2, ':', C_SUB)
        # EDIT 边框（焦点时主色）
        focus = user32.GetFocus()
        for eid, ey in ((ID_IP, IP_Y), (ID_PORT, IP_Y), (ID_CODE, CODE_Y)):
            eh = ctx['edits'][eid]
            rc = wt.RECT()
            user32.GetWindowRect(eh, ctypes.byref(rc))
            pt = wt.POINT(rc.left, rc.top)
            user32.ScreenToClient(ctx['hwnd'], ctypes.byref(pt))
            ew = rc.right - rc.left
            line = C_EDIT_FOCUS if focus == eh else C_EDIT_LINE
            _round_rect(hdc, pt.x - 2, pt.y - 2, ew + 4, EDIT_H + 4, 6, C_EDIT_BG, line)
        # 唯一的大按钮
        gx, gy, gw, gh = ctx['hotspots'][ID_GO]
        hover = ctx['hover'] == ID_GO
        if ctx['busy'] and ctx['busy_row'] is None:
            dots = '.' * (1 + ctx['tick'] % 3)
            label = ctx['busy_text'] + dots
            _round_rect(hdc, gx, gy, gw, gh, 10, C_PRIMARY_LT)
            tw = _text_size(hdc, f_btn, label)[0]
            _draw_text(hdc, f_btn, gx + (gw - tw) // 2, gy + 11, label, C_PRIMARY)
        else:
            _round_rect(hdc, gx, gy, gw, gh, 10, C_PRIMARY_DK if hover else C_PRIMARY)
            label = '连接手机'
            tw = _text_size(hdc, f_btn, label)[0]
            _draw_text(hdc, f_btn, gx + (gw - tw) // 2, gy + 11, label, (255, 255, 255))
    else:
        _draw_text(hdc, f_small, PAD, USB_HINT_Y,
                   '提示：手机上弹出「允许 USB 调试」时勾选「一律允许」，以后即插即用。', C_FAINT)

    # 内联状态区（错误红 / 成功绿 / 进行中主色 / 普通灰）
    st = ctx['status_text']
    if st:
        kind = ctx['status_kind']
        fg = {'err': C_ERR, 'ok': C_OK, 'busy': C_PRIMARY}.get(kind, C_SUB)
        bg = {'err': C_ERR_LT, 'ok': C_OK_LT, 'busy': C_PRIMARY_LT}.get(kind, C_BG)
        lines = _wrap_text(hdc, f_small, st, W - 2 * PAD - 24)
        bh = 12 + len(lines) * 18
        _round_rect(hdc, PAD, STATUS_Y, W - 2 * PAD, bh, 8, bg)
        for i, ln in enumerate(lines):
            _draw_text(hdc, f_small, PAD + 12, STATUS_Y + 7 + i * 18, ln, fg)

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
    wifi = tab == 'wifi'
    for eid in (ID_IP, ID_PORT, ID_CODE):
        user32.ShowWindow(ctx['edits'][eid], SW_SHOW if wifi else SW_HIDE)
    _rebuild_hotspots(ctx)
    _invalidate(ctx)


def _start_job(ctx, kind, payload, row=None, busy_text='正在连接'):
    if ctx['busy'] or ctx['closing']:
        return
    ctx['busy'] = True
    ctx['busy_row'] = row
    ctx['busy_text'] = busy_text
    ctx['tick'] = 0
    user32.SetTimer(ctx['hwnd'], 1, 400, None)
    _status(ctx, busy_text + '…', 'busy')
    threading.Thread(target=_worker_job, args=(ctx, kind, payload), daemon=True).start()


def _worker_job(ctx, kind, payload):
    """在工作线程里跑 adb，结果放到 ctx 里再 PostMessage 回窗口。"""
    try:
        if kind == 'refresh':
            online = list_devices_detailed(ctx['adb'], ctx['runner'])
            disc = parse_mdns_all(ctx['runner']([ctx['adb'], 'mdns', 'services'])[1])
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
                elif r['state'] == 'found':
                    m = re.match(r'^adb-[0-9a-f]+-(.+)$', r['name'])
                    if m:
                        r['name'] = m.group(1)
            ctx['job'] = ('refresh', rows, '')
        elif kind == 'pair':
            ok, msg, serial = pair_then_connect(ctx['adb'], payload[0], payload[1],
                                                ctx['runner'])
            ctx['job'] = ('pair', (ok, msg, serial), '')
        elif kind == 'connect':
            ok, msg, serial = connect_only(ctx['adb'], payload, ctx['runner'])
            ctx['job'] = ('connect', (ok, msg, serial), '')
        elif kind == 'reconnect':
            ok, msg, serial = reconnect_known(ctx['adb'], payload, ctx['runner'])
            ctx['job'] = ('reconnect', (ok, msg, serial), '')
    except Exception as exc:
        ctx['job'] = (kind, '', str(exc))
    user32.PostMessageW(ctx['hwnd'], WM_APP_JOB, 0, 0)


def _on_job_done(ctx):
    job = ctx.get('job')
    if not job:
        return
    ctx['job'] = None
    kind = job[0]
    user32.KillTimer(ctx['hwnd'], 1)
    ctx['busy'] = False
    ctx['busy_row'] = None
    if job[2]:   # 工作线程抛异常（refresh 也算，不能静默当成 0 台设备）
        if ctx['log']:
            ctx['log']('wizard: %s 异常 %s' % (kind, job[2]))
        _status(ctx, friendly_error(job[2], '连接'), 'err')
        _invalidate(ctx)
        return
    if kind == 'refresh':
        rows = job[1] or []
        ctx['rows'] = rows
        _rebuild_hotspots(ctx)
        n_on = sum(1 for r in rows if r['state'] == 'online')
        if rows:
            if n_on:
                _status(ctx, '找到 %d 台设备（%d 台已连接），点已连接的设备即可投屏。'
                        % (len(rows), n_on), 'info')
            else:
                _status(ctx, '找到 %d 台设备，点设备右侧「连接」即可投屏。'
                        % len(rows), 'info')
        else:
            _status(ctx, '', 'info')
        if ctx['log']:
            ctx['log']('wizard: devices=%s' % ([r['serial'] for r in rows],))
        _invalidate(ctx)
        return
    ok, msg, serial = job[1] if isinstance(job[1], tuple) else (False, str(job[1]), '')
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
    for r in ctx['rows']:
        if r['serial'] == serial or r['addr'] == serial:
            name = r['name']
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
            if n >= 2:
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
    if hid == ID_GO:
        ip_port = join_ip_port(_text(ctx['edits'][ID_IP]),
                               _text(ctx['edits'][ID_PORT]))
        if not ip_port:
            _status(ctx, '请在两个框里分别填手机配对弹窗里的 IP 和端口，'
                         '例如 192.168.1.5 和 43847（冒号不用输）。', 'err')
            return
        code = _text(ctx['edits'][ID_CODE]).strip()
        if not valid_code(code):
            _status(ctx, '配对码是手机配对弹窗里的 6 位数字。', 'err')
            return
        _start_job(ctx, 'pair', (ip_port, code), busy_text='正在配对并连接')
        return
    if hid >= ID_ROW_BASE:
        i = hid - ID_ROW_BASE
        rows = ctx['visible_rows']
        if i >= len(rows):
            return
        r = rows[i]
        if r['state'] == 'online':
            _finish(ctx, r['serial'])
        elif r['state'] == 'found':
            _start_job(ctx, 'connect', r['addr'], row=i)
        else:
            _start_job(ctx, 'reconnect',
                       {'serial': r['serial'], 'addr': r['addr'], 'name': r['name']},
                       row=i)
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
        _notify_close(ctx)
        for f in ('f_title', 'f_body', 'f_small', 'f_btn'):
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
        wy = wa.top + max(0, ((wa.bottom - wa.top) - WIN_H) // 2)
        style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN
        hwnd = user32.CreateWindowExW(0, 'MoyuConnectWizard', '魔芋 · 连接手机',
                                      style, wx, wy, WIN_W, WIN_H, None, None, hinst, None)
        if not hwnd:
            if on_log:
                on_log('wizard: 创建窗口失败 err=%s' % ctypes.get_last_error())
            return
        if hicon:
            # 类图标只对新建窗口生效；显式再发一次，确保标题栏与任务栏都换掉
            user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)   # ICON_SMALL
            user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)   # ICON_BIG
        # 客户区精确到 WIN_W x WIN_H（CreateWindow 的尺寸含边框）
        rc = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        if rc.right != WIN_W or rc.bottom != WIN_H:
            wr = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(wr))
            dw = WIN_W - rc.right
            dh = WIN_H - rc.bottom
            user32.SetWindowPos(hwnd, None, 0, 0, (wr.right - wr.left) + dw,
                                (wr.bottom - wr.top) + dh, 0x0002 | 0x0004)  # NOMOVE|NOZORDER
        ctx = {'hwnd': hwnd, 'adb': adb, 'on_done': on_done, 'log': on_log,
               'on_close': on_close, 'closed': False, 'job': None, 'busy': False,
               'busy_row': None, 'busy_text': '', 'tick': 0, 'hicon': hicon,
               'known': known or [], 'runner': runner,
               'tab': 'wifi', 'rows': [], 'visible_rows': [],
               'hotspots': {}, 'hover': 0, 'pressed': 0, 'tracking': False,
               'status_text': '点右上角「刷新」查找设备；第一次用无线请在下方配对。',
               'status_kind': 'info', 'edits': {}, 'cues': {},
               'toast': '', 'closing': False}
        ctx['f_title'] = _font_create(19, 600)
        ctx['f_body'] = _font_create(14, 400)
        ctx['f_small'] = _font_create(12, 400)
        ctx['f_btn'] = _font_create(13, 600)
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
        _rebuild_hotspots(ctx)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)
        user32.SetForegroundWindow(hwnd)
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