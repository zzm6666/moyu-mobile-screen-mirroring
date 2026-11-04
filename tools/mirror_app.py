# -*- coding: utf-8 -*-
"""魔芋 —— 手机投屏小窗（毛玻璃外框 + 自绘操作栏版）。

- 自绘无边框圆角窗口：顶部毛玻璃操作栏 + 右侧关闭按钮，可拖动、可缩放
- scrcpy 画面作为独立窗口垫在外框中间的透明洞里，按手机宽高比自适应、不变形
- 视频区点击穿透到 scrcpy（保留鼠标反控），操作栏/边框用于拖动和缩放
- 托盘图标：左键显示/隐藏，右键菜单「显示/隐藏投屏」「退出」

依赖：同目录 scrcpy-win64-v5.0.1 与 app.ico
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import threading
import time

from PIL import (Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont,
                 ImageGrab, ImageStat)

APP_NAME = '魔芋'
CLASS_TRAY = 'CapyMirrorTray'
CLASS_FRAME = 'CapyMirrorFrame'

TOOLBAR_H = 38
FRAME = 0  # 视频洞与外框的内边距（0=视频直接贴边，无间隙）
RESIZE_EDGE = 4  # 窗口边缘的缩放热区宽度（独立于 FRAME，用于拖拽缩放）
EDGE_GRAB = 6  # 全局钩子判定「抓边缩放」的带宽：比 RESIZE_EDGE 宽一点更好抓
CLOSE_W = 44
MIN_VIDEO = 180  # 视频区短边下限（缩放时保比例的下限）
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
WM_TRAY = 0x8001
WM_APP_RAISE = 0x8002  # 点击视频区时请求整体置前
TIMER_RESIZE = 3  # 缩放期间的轮询定时器（鼠标压在视频区上时收不到 WM_MOUSEMOVE）
ID_TOGGLE = 1001
ID_EXIT = 1002
CREATE_NO_WINDOW = 0x08000000

# --- 操作栏视觉 ---
CORNER_R = 10  # 整窗四角统一圆角半径
SS = 4  # 超采样倍率：圆角/文字/图形统一抗锯齿，边缘更平滑
TINT_TOP = (255, 255, 255, 120)  # 毛玻璃竖向渐变（上）：透出背后的模糊色块
TINT_BOTTOM = (238, 243, 251, 96)  # 毛玻璃竖向渐变（下）：越往下越透，形成玻璃厚度感
# 深色背景用深色玻璃：白玻璃压在暗底上会把背后糊成一整片均匀灰，反而看不出玻璃
TINT_TOP_DARK = (22, 24, 30, 88)
TINT_BOTTOM_DARK = (12, 14, 20, 64)
TEXT_COLOR = (56, 58, 66, 255)
TEXT_COLOR_DARK = (244, 247, 252, 255)
CLOSE_X_COLOR = (146, 149, 158, 255)  # 关闭 × 默认浅色
CLOSE_X_HOVER = (44, 46, 54, 255)  # 悬停时 × 变深（无红底）
CLOSE_X_COLOR_DARK = (196, 200, 210, 255)
CLOSE_X_HOVER_DARK = (255, 255, 255, 255)
MARGIN = 16  # 窗口与屏幕右边/底部（任务栏上沿）的统一边距
VIDEO_LONG = 572  # 视频区长边（竖屏=高，横屏=宽），保持不变形切换
BLUR_RADIUS = 12  # 毛玻璃背景模糊半径：太大（34）会把背后内容糊成一片灰，看不出玻璃
ICON_SIZE = 20  # 操作栏左侧应用图标边长
ICON_LEFT = 9  # 图标左边距
SATURATION = 1.6  # 背景饱和度增益：毛玻璃透出的颜色更"活"
GRAIN = 6  # 操作栏颗粒感强度（亚克力噪点，sigma），0 关闭

# --- 窗口 Z 序 ---
HWND_TOP = 0
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
TRAY_CLICK_GUARD = 0.35  # 秒；双击托盘只算一次，避免「显示→立刻又隐藏」
WH_MOUSE_LL = 14

DEFAULT_CFG = {
    'serial': '192.168.137.198:38493',
    'title': '魔芋',
    'x': 1636,
    'y': 448,
    'w': 260,
    'h': 572,
    'fps': 60,
    'bitrate': '4M',
    'max_size': 720,
}


def base_dir():
    if getattr(sys, 'frozen', False):
        here = os.path.dirname(sys.executable)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(here, 'scrcpy-win64-v5.0.1')):
        return here
    for cand in (r'D:\moyu\tools',):
        if os.path.isdir(os.path.join(cand, 'scrcpy-win64-v5.0.1')):
            return cand
    return here


BASE = base_dir()


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    path = os.path.join(BASE, 'config.json')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


CFG = load_cfg()
TITLE = CFG['title']
SCRCPY_DIR = os.path.join(BASE, 'scrcpy-win64-v5.0.1')
ADB = os.path.join(SCRCPY_DIR, 'adb.exe')
SCRCPY = os.path.join(SCRCPY_DIR, 'scrcpy.exe')
ICO = os.path.join(getattr(sys, '_MEIPASS', BASE), 'app.ico')
# 操作栏里贴的应用图标（圆形，圆外透明）；app.ico 供托盘与 exe 资源使用
APP_ICON = os.path.join(getattr(sys, '_MEIPASS', BASE), 'app_icon.png')


def log(msg):
    try:
        with open(os.path.join(BASE, 'app.log'), 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


log('--- start, BASE=%s frozen=%s' % (BASE, getattr(sys, 'frozen', False)))

_ICON_CACHE = {}


def app_icon(size):
    """操作栏用的圆形应用图标（按边长缓存；读不到时退化为全透明）。"""
    size = max(1, int(size))
    hit = _ICON_CACHE.get(size)
    if hit is not None:
        return hit
    try:
        im = Image.open(APP_ICON).convert('RGBA')
        if im.size != (size, size):
            im = im.resize((size, size), Image.LANCZOS)
    except Exception:
        im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    _ICON_CACHE[size] = im
    return im


user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
shell32 = ctypes.WinDLL('shell32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
winmm = ctypes.WinDLL('winmm')
dwmapi = ctypes.WinDLL('dwmapi')
msimg32 = ctypes.WinDLL('msimg32')

user32.SetProcessDPIAware()

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wt.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON),
        ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH),
        ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR),
    ]


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]


class MARGINS(ctypes.Structure):
    _fields_ = [('cxLeftWidth', ctypes.c_int), ('cxRightWidth', ctypes.c_int),
                ('cyTopHeight', ctypes.c_int), ('cyBottomHeight', ctypes.c_int)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ('hdc', wt.HDC), ('fErase', wt.BOOL), ('rcPaint', RECT),
        ('fRestore', wt.BOOL), ('fIncUpdate', wt.BOOL),
        ('rgbReserved', ctypes.c_byte * 32),
    ]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ('ptReserved', POINT), ('ptMaxSize', POINT), ('ptMaxPosition', POINT),
        ('ptMinTrackSize', POINT), ('ptMaxTrackSize', POINT),
    ]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('dwFlags', wt.DWORD),
                ('hwndTrack', wt.HWND), ('dwHoverTime', wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_byte), ('BlendFlags', ctypes.c_byte),
                ('SourceConstantAlpha', ctypes.c_byte), ('AlphaFormat', ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wt.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),
        ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
        ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', ctypes.c_long),
        ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wt.DWORD * 3)]


class GUID(ctypes.Structure):
    _fields_ = [('Data1', wt.DWORD), ('Data2', wt.WORD), ('Data3', wt.WORD),
                ('Data4', ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wt.DWORD), ('hWnd', wt.HWND), ('uID', wt.UINT), ('uFlags', wt.UINT),
        ('uCallbackMessage', wt.UINT), ('hIcon', wt.HICON), ('szTip', wt.WCHAR * 128),
        ('dwState', wt.DWORD), ('dwStateMask', wt.DWORD), ('szInfo', wt.WCHAR * 256),
        ('uVersion', wt.UINT), ('szInfoTitle', wt.WCHAR * 64), ('dwInfoFlags', wt.DWORD),
        ('guidItem', GUID), ('hBalloonIcon', wt.HICON),
    ]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    """Shell_NotifyIconGetRect 的入参：定位托盘图标矩形（诊断/点击命中用）。"""
    _fields_ = [
        ('cbSize', wt.DWORD), ('hWnd', wt.HWND), ('uID', wt.UINT),
        ('guidItem', ctypes.c_uint32 * 4),
    ]


def tray_icon_rect(hwnd, uid=1):
    """返回托盘图标在屏幕上的矩形；图标被收进溢出面板时返回 None。"""
    try:
        shell32.Shell_NotifyIconGetRect.argtypes = [
            ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(RECT)]
        shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
    except Exception:
        return None
    nii = NOTIFYICONIDENTIFIER()
    nii.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
    nii.hWnd = hwnd
    nii.uID = uid
    rc = RECT()
    hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(nii), ctypes.byref(rc))
    if hr != 0:
        return None
    return rc


user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wt.BOOL
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.MoveWindow.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.BOOL]
user32.MoveWindow.restype = wt.BOOL
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(RECT), wt.HBRUSH]
user32.FillRect.restype = ctypes.c_int
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wt.HICON
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.DestroyWindow.argtypes = [wt.HWND]
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetCursor.argtypes = [wt.HANDLE]
user32.SetCursor.restype = wt.HANDLE
user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
user32.TrackMouseEvent.restype = wt.BOOL
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                       wt.HDC, ctypes.c_void_p, wt.COLORREF,
                                       ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.c_void_p, wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = wt.HRGN
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wt.DWORD

dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wt.HWND, ctypes.POINTER(MARGINS)]
dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long

msimg32.AlphaBlend.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint]
msimg32.AlphaBlend.restype = wt.BOOL

user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
user32.SystemParametersInfoW.restype = wt.BOOL
SPI_GETWORKAREA = 0x0030

user32.SetCapture.argtypes = [wt.HWND]
user32.SetCapture.restype = wt.HWND
user32.ReleaseCapture.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wt.BOOL

_PROC_REFS = []
_FONT_CACHE = {}

state = {
    'tray': None, 'frame': None, 'child': None, 'proc': None,
    'tray_click_ts': 0.0,
    'started_at': time.time(), 'quitting': False, 'error_shown': False,
    'aspect': 1080 / 2376, 'video_rect': (0, 0, 0, 0), 'close_hover': False,
    'orient': None, 'user_moved': False, 'region_key': None,
    'resizing': False, 'resize_hit': 0, 'resize_pt': (0, 0), 'resize_rect': (0, 0, 0, 0),
    'blur': None, 'blur_key': None, 'blur_ts': 0.0, 'moving': False,
    'drawn_size': None,  # 上次真正绘制操作栏时的 (W, H)，纯拖位移时用来跳过重绘
    'hook': None,
    'bar_dark': False,  # 操作栏背后是深色底 → 用深色玻璃 tint
}


def target_hwnd():
    return user32.FindWindowW(None, TITLE)


def mirror_visible():
    """窗口是否真的「在用户眼前」：可见且是最前台。

    只判断 IsWindowVisible 会把「被别的窗口盖住」也算作显示状态，导致托盘菜单
    文案与实际行为对不上（菜单写「隐藏投屏」，点下去却是把它提到前面）。
    """
    f = state['frame']
    if not f or not user32.IsWindowVisible(f):
        return False
    return user32.GetForegroundWindow() in (f, state['child'])


def has_audio_device():
    try:
        return winmm.waveOutGetNumDevs() > 0
    except Exception:
        return False


def get_phone_aspect():
    try:
        r = subprocess.run([ADB, '-s', CFG['serial'], 'shell', 'wm', 'size'],
                           stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=8)
        m = re.search(r'(\d+)x(\d+)', r.stdout.decode('utf-8', errors='ignore'))
        if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
            return int(m.group(1)) / int(m.group(2))
    except Exception:
        pass
    return 1080 / 2376


def start_scrcpy():
    state['aspect'] = get_phone_aspect()
    try:
        subprocess.run([ADB, 'connect', CFG['serial']], stdin=subprocess.DEVNULL,
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=8)
    except Exception:
        pass
    args = [SCRCPY, '-s', CFG['serial'],
            '--window-title=' + TITLE,
            '--window-borderless',
            '--window-x=0', '--window-y=0',
            '--window-width=%d' % CFG['w'], '--window-height=%d' % CFG['h'],
            '--max-fps=%d' % CFG['fps'],
            '--video-bit-rate=' + str(CFG['bitrate']),
            '-m', str(CFG['max_size'])]
    if not has_audio_device():
        args.append('--no-audio')
    state['proc'] = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=CREATE_NO_WINDOW)
    log('scrcpy launched pid=%s aspect=%.4f' % (state['proc'].pid, state['aspect']))


def _font(size):
    if size not in _FONT_CACHE:
        for path in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyh.ttf',
                     r'C:\Windows\Fonts\simhei.ttf'):
            if os.path.exists(path):
                try:
                    _FONT_CACHE[size] = ImageFont.truetype(path, size)
                    return _FONT_CACHE[size]
                except Exception:
                    pass
        _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


def calc_video_rect(W, H):
    cw = W - 2 * FRAME
    chh = H - TOOLBAR_H - FRAME
    if cw <= 0 or chh <= 0:
        return 0, 0, 0, 0
    aspect = state['aspect']
    if cw / aspect <= chh:
        vw, vh = cw, int(cw / aspect)
    else:
        vh, vw = chh, int(chh * aspect)
    vx = FRAME + (cw - vw) // 2
    vy = TOOLBAR_H + (chh - vh) // 2
    return vx, vy, vw, vh


def frame_size_for_aspect(aspect):
    """按手机宽高比返回 (外框W, 外框H, 视频vw, 视频vh)：长边固定 VIDEO_LONG。

    横屏时高=视频高+操作栏（无 letterbox），保证视频区完整可见不变形。
    """
    if aspect > 1.0:
        vw = VIDEO_LONG
        vh = int(vw / aspect)
    else:
        vh = VIDEO_LONG
        vw = int(vh * aspect)
    if vw < 40:
        vw = 40
    if vh < 40:
        vh = 40
    return vw + 2 * FRAME, vh + TOOLBAR_H + FRAME, vw, vh


def clamp_into_workarea(x, y, w, h, wa_w, wa_h, margin):
    """把窗口位置 clamp 进工作区（右/下边缘留 margin）。窗口比工作区大时贴 margin。"""
    nx = x
    ny = y
    if nx + w > wa_w - margin:
        nx = wa_w - margin - w
    if ny + h > wa_h - margin:
        ny = wa_h - margin - h
    if nx < margin:
        nx = margin
    if ny < margin:
        ny = margin
    return nx, ny


def lock_aspect(l, t, r, b, hit, aspect, min_video=MIN_VIDEO):
    """按手机宽高比锁定外框尺寸，返回调整后的 (l, t, r, b)。

    横向边缘（左/右/角）以宽为准推高，纵向边缘（上/下）以高为准推宽；被拖动的
    边保持不动、对边吸收尺寸变化，因此缩放过程中视频区永远等于手机宽高比，
    不会出现黑边或拉伸变形（需求3）。
    """
    w = max(2 * FRAME + min_video, r - l)
    h = max(TOOLBAR_H + FRAME + min_video, b - t)
    horizontal = hit in (10, 11, 13, 14, 16, 17)
    if horizontal:
        vw = w - 2 * FRAME
        vh = int(round(vw / aspect))
        if vh < min_video:
            vh = min_video
            vw = int(round(vh * aspect))
            w = vw + 2 * FRAME
        h = vh + TOOLBAR_H + FRAME
    else:
        vh = h - TOOLBAR_H - FRAME
        vw = int(round(vh * aspect))
        if vw < min_video:
            vw = min_video
            vh = int(round(vw / aspect))
            h = vh + TOOLBAR_H + FRAME
        w = vw + 2 * FRAME
    l = r - w if hit in (10, 13, 16) else l
    r = l + w
    t = b - h if hit in (12, 13, 14) else t
    b = t + h
    return l, t, r, b


def rect_edge_hit(r, x, y, grab=EDGE_GRAB):
    """屏幕点 (x, y) 落在外框矩形 r 的哪条抓边：返回 10-17（同 WM_NCHITTEST），否则 0。

    视频区被 scrcpy 子窗口整块覆盖，frame 根本收不到那些点的 WM_NCHITTEST，
    所以边缘缩放改由全局鼠标钩子用这个纯函数判断。
    """
    if r is None:
        return 0
    if not (r.left <= x < r.right and r.top <= y < r.bottom):
        return 0
    left = x < r.left + grab
    right = x >= r.right - grab
    top = y < r.top + grab
    bottom = y >= r.bottom - grab
    if left and top:
        return 13
    if right and top:
        return 14
    if left and bottom:
        return 16
    if right and bottom:
        return 17
    if left:
        return 10
    if right:
        return 11
    if top:
        return 12
    if bottom:
        return 15
    return 0


def clamp_radius(w, h, r):
    """圆角半径不许超过短边一半，否则 CreateRoundRectRgn 画出畸形区域。"""
    if w <= 0 or h <= 0:
        return 0
    return max(0, min(int(r), min(int(w), int(h)) // 2))


def round_region(w, h, r):
    """视频窗口的裁剪区域句柄（SetWindowRgn 成功后系统接管，不要自行删除）。

    只圆**下面两角**：上沿紧贴操作栏，必须保持直角才能无缝拼接；把外框顶边
    抬到 -r 处，弧线落在窗口之外，于是上沿自然变直、下沿两角仍是半径 r 的圆角。
    CreateRoundRectRgn 的椭圆参数是直径，故传 r*2；右/下边界开区间，+1 铺满。
    """
    w, h = int(w), int(h)
    r = clamp_radius(w, h, r)
    return gdi32.CreateRoundRectRgn(0, -r, w + 1, h + 1, r * 2, r * 2)


def apply_child_round_region(vw, vh):
    """给视频窗口（scrcpy）套圆角裁剪区：四角外真正透出桌面，不是覆盖色块。

    scrcpy 的 SDL 窗口保持普通不透明窗口，只裁形状——不碰它的渲染路径，
    视频反控（点击穿透到 scrcpy）不受影响（需求2）。
    """
    ch = state['child']
    if not ch or not user32.IsWindow(ch) or vw <= 0 or vh <= 0:
        return
    key = (int(ch), vw, vh, CORNER_R)
    if state.get('region_key') == key:
        return
    hrgn = round_region(vw, vh, CORNER_R)
    if user32.SetWindowRgn(ch, hrgn, True):
        state['region_key'] = key
    else:
        gdi32.DeleteObject(hrgn)


def premultiply(img):
    r, g, b, a = img.split()
    return Image.merge('RGBA', (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))


def blank_layered(hwnd, W, H):
    """把分层窗口刷成全透明：让窗口在画面上「让开」，好让桌面的真实内容露出来。"""
    draw_layered(hwnd, W, H, bytes(W * H * 4))


def capture_blur(hwnd):
    """截取操作栏背后**真实的桌面内容**并模糊，作为毛玻璃底。

    不能直接 Grad 当前屏幕 —— 此时本窗口正是可见状态，截到的是自己刚画的那条
    操作栏，模糊之后永远是一片均匀的浅色，看起来「根本没有毛玻璃」（实测条带
    内 RGB 标准差只有 5~9，而真实背景在 40+）。所以先把窗口刷成全透明、
    等合成器吐一帧，再只截操作栏那一条；update_frame 随后会立刻重画回来。
    """
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    W = r.right - r.left
    H = r.bottom - r.top
    if W <= 0 or H <= 0:
        return None
    th = max(1, min(TOOLBAR_H, H))
    blank_layered(hwnd, W, H)
    try:
        dwmapi.DwmFlush()
    except Exception:
        pass
    # 关窗后合成器需要一小段时间才真正把透明帧刷到屏幕上，
    # 不等这一下偶尔会截到「上一帧的自己」（条带标准差 0）
    time.sleep(0.012)
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.top + th), all_screens=True)
    img = img.convert('RGB')
    # 背景亮暗决定玻璃是白玻璃还是深色玻璃（自适应，和 Windows 亚克力一致）
    st = ImageStat.Stat(img)
    lum = 0.299 * st.mean[0] + 0.587 * st.mean[1] + 0.114 * st.mean[2]
    state['bar_dark'] = lum < 128
    try:
        log('blur: strip %dx%d stddev=%.1f lum=%.1f dark=%s'
            % (img.width, img.height, st.stddev[0], lum, state['bar_dark']))
    except Exception:
        pass
    img = img.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    # 把透出来的颜色调鲜艳、提亮一点（相当于 Windows 亚克力的亮度层），
    # 否则隔着半透明 tint 看就是一片灰白，不像玻璃
    img = ImageEnhance.Color(img).enhance(SATURATION)
    return ImageEnhance.Brightness(img).enhance(1.12)


def build_frame_image(W, H, vx, vy, vw, vh, blur_bg=None):
    """只绘制顶部操作栏：上方两个圆角、下方直角与视频无缝衔接；视频区全透明。

    整条操作栏在 SS 倍超采样画布上绘制后 LANCZOS 缩回，圆角/文字/图形统一抗锯齿。
    """
    th = min(TOOLBAR_H, H)
    sW, sH = W * SS, th * SS

    # 背景：模糊后的屏幕内容（毛玻璃底）。capture_blur 现在只截操作栏那一条，
    # 高度就等于 th；老缓存若是整窗高度，也只取顶部 th 行。
    if blur_bg is not None:
        bg = blur_bg.convert('RGBA')
        if bg.size[1] != th:
            bg = bg.crop((0, 0, min(bg.size[0], W), min(bg.size[1], th)))
        if bg.size != (W, th):
            bg = bg.resize((W, th), Image.LANCZOS)
        bg = bg.resize((sW, sH), Image.LANCZOS)
    else:
        bg = Image.new('RGBA', (sW, sH), (248, 249, 252, 255))

    # 竖向渐变 tint：上亮下略沉，比纯半透明更有玻璃质感；底色暗时换深色玻璃
    dark = bool(state.get('bar_dark'))
    top_c, bot_c = (TINT_TOP_DARK, TINT_BOTTOM_DARK) if dark else (TINT_TOP, TINT_BOTTOM)
    rows = []
    for y in range(sH):
        t = y / max(1, sH - 1)
        rows.append(tuple(int(top_c[i] + (bot_c[i] - top_c[i]) * t)
                          for i in range(4)))
    grad = Image.new('RGBA', (1, sH))
    grad.putdata(rows)
    grad = grad.resize((sW, sH))
    bar = Image.alpha_composite(bg, grad)

    # UI 图层（独立图层再合成，保证半透明像素正确）
    ui = Image.new('RGBA', (sW, sH), (0, 0, 0, 0))
    d = ImageDraw.Draw(ui)
    d.line([(0, 0), (sW, 0)], fill=(255, 255, 255, 110 if dark else 120),
           width=SS)  # 顶部玻璃高光
    # 底部内侧高光：把操作栏与视频的接缝压成一条亮线，看起来是一体式玻璃面板
    d.line([(0, sH - SS), (sW, sH - SS)], fill=(255, 255, 255, 60 if dark else 82),
           width=SS)

    # 左上角：应用图标（圆形，圆外透明），替代原先手绘的鱼形标记
    isz = ICON_SIZE * SS
    ui.alpha_composite(app_icon(isz), (ICON_LEFT * SS, (sH - isz) // 2))

    # 标题：垂直居中
    f_title = _font(12 * SS)
    bb = d.textbbox((0, 0), APP_NAME, font=f_title)
    ty = (sH - (bb[3] - bb[1])) // 2 - bb[1]
    d.text(((ICON_LEFT + ICON_SIZE + 9) * SS, ty), APP_NAME, font=f_title,
           fill=TEXT_COLOR_DARK if dark else TEXT_COLOR)

    # 关闭 ×：自绘圆头细线，hover 仅变深，不加红底
    cx, cy = sW - (CLOSE_W * SS) // 2, sH // 2
    half, lw = 6 * SS, 2 * SS
    if dark:
        x_color = CLOSE_X_HOVER_DARK if state['close_hover'] else CLOSE_X_COLOR_DARK
    else:
        x_color = CLOSE_X_HOVER if state['close_hover'] else CLOSE_X_COLOR
    d.line([(cx - half, cy - half), (cx + half, cy + half)], fill=x_color, width=lw)
    d.line([(cx + half, cy - half), (cx - half, cy + half)], fill=x_color, width=lw)
    rad = lw // 2
    for ex, ey in ((cx - half, cy - half), (cx + half, cy + half),
                   (cx + half, cy - half), (cx - half, cy + half)):
        d.ellipse([ex - rad, ey - rad, ex + rad, ey + rad], fill=x_color)
    bar = Image.alpha_composite(bar, ui)

    # 圆角 mask：只有左上/右上两个圆角；bbox 底部多伸出 CORNER_R，
    # 使底部圆角弧线落在画布之外，下边缘保持直线，与视频区严丝合缝
    mask = Image.new('L', (sW, sH), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, sW - 1, sH + CORNER_R * SS], radius=CORNER_R * SS, fill=255)
    # 蒙版单独用 BOX 缩小：灰度过渡单调，无 LANCZOS 振铃造成的凹陷
    mask = mask.resize((W, th), Image.BOX)

    bar = bar.resize((W, th), Image.LANCZOS)
    bar.putalpha(ImageChops.multiply(bar.getchannel('A'), mask))
    # 颗粒感（亚克力材质的关键）：真实毛玻璃/亚克力都有细噪点，纯色块永远不像玻璃
    if GRAIN > 0:
        g = Image.effect_noise((W, th), GRAIN).convert('L')
        rgb = ImageChops.add(bar.convert('RGB'), Image.merge('RGB', (g, g, g)), 1.0, -128)
        bar = Image.merge('RGBA', (rgb.getchannel('R'), rgb.getchannel('G'),
                                   rgb.getchannel('B'), bar.getchannel('A')))
    # 强制底边完全不透明，与视频区严丝合缝
    a = bar.getchannel('A')
    ImageDraw.Draw(a).line([(0, th - 1), (W, th - 1)], fill=255)
    bar.putalpha(a)

    out = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    out.paste(bar, (0, 0))
    return out


def draw_layered(hwnd, W, H, raw_bgra):
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = W
    bmi.bmiHeader.biHeight = -H
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    pbits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(pbits), None, 0)
    ctypes.memmove(pbits, raw_bgra, len(raw_bgra))
    old = gdi32.SelectObject(hdc_mem, hbmp)
    size = SIZE(W, H)
    blend = BLENDFUNCTION(0, 0, 255, 1)
    ret = user32.UpdateLayeredWindow(hwnd, hdc_screen, None, ctypes.byref(size), hdc_mem,
                                     ctypes.byref(POINT(0, 0)), 0, ctypes.byref(blend), 2)
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(None, hdc_screen)


def move_to_bottom_right():
    """把外框窗口定位到屏幕右下角，右边距与底边距（到任务栏上沿）一致。"""
    f = state['frame']
    if not f:
        return
    r = RECT()
    user32.GetWindowRect(f, ctypes.byref(r))
    W = r.right - r.left
    H = r.bottom - r.top
    wa = RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
    x = wa.right - W - MARGIN
    y = wa.bottom - H - MARGIN
    if x < MARGIN:
        x = MARGIN
    if y < MARGIN:
        y = MARGIN
    user32.SetWindowPos(f, None, x, y, 0, 0, 0x0001 | 0x0004)  # SWP_NOSIZE | SWP_NOZORDER


def ensure_frame_orientation():
    """根据手机方向切换外框横竖屏（长边固定 VIDEO_LONG，短边按宽高比重算）。

    user_moved（用户手动拖过）时保持当前 x/y 只 clamp 进工作区——右下/上边不
    遮挡；未移动过则维持右下角等距初始位（需求4/5）。
    """
    f = state['frame']
    if not f:
        return
    orient = 'landscape' if state['aspect'] > 1.0 else 'portrait'
    if orient == state['orient']:
        return
    state['orient'] = orient
    fw, fh, vw, vh = frame_size_for_aspect(state['aspect'])
    r = RECT()
    user32.GetWindowRect(f, ctypes.byref(r))
    x, y = r.left, r.top
    if not state.get('user_moved'):
        wa = RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
        x = wa.right - fw - MARGIN
        y = wa.bottom - fh - MARGIN
    x, y = clamp_into_workarea(x, y, fw, fh, wa_w(), wa_h(), MARGIN)
    user32.SetWindowPos(f, None, x, y, fw, fh, 0x0004)  # SWP_NOZORDER
    update_frame(f)
    log('orientation -> %s (%dx%d) at (%d,%d) moved=%s'
        % (orient, fw, fh, x, y, state.get('user_moved')))


def wa_w():
    r = RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.right - r.left


def wa_h():
    r = RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.bottom - r.top


def position_scrcpy(vx, vy, vw, vh):
    ch = state['child']
    f = state['frame']
    if not ch or not f or not user32.IsWindow(ch):
        return
    fr = RECT()
    user32.GetWindowRect(f, ctypes.byref(fr))
    # 显式插到 frame 正下方：不用 HWND_TOP，避免 Z 序被其它机制反复打乱
    user32.SetWindowPos(ch, f, fr.left + vx, fr.top + vy, vw, vh, SWP_NOACTIVATE)
    apply_child_round_region(vw, vh)


def sync_child_zorder():
    """维护视频窗口紧跟 frame 下方，杜绝别的窗口插进两者之间遮住视频。"""
    f = state['frame']
    ch = state['child']
    if not f or not ch or not user32.IsWindow(ch):
        return
    user32.SetWindowPos(ch, f, 0, 0, 0, 0,
                        SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOMOVE)


def force_foreground(hwnd):
    """把窗口真正激活到前台。

    点托盘时输入属于任务栏，普通 SetForegroundWindow 会被 Windows 前台锁拒绝，
    窗口虽然显示了却拿不到焦点（看起来像「点了没反应」）。这里临时挂到当前前台
    线程的输入队列再激活，激活完立刻解挂。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(tid_me, tid_fg, False)


def raise_mirror():
    """激活时把「操作栏 + 视频」作为一个整体提到 Z 序最前（普通层级，非 topmost）。

    注意：单用 SetWindowPos(HWND_TOP) 在本机多窗口叠加时实测无效——窗口仍留在
    其它普通窗口之下（点击托盘后依然看不见）。改用「先临时 TOPMOST、立刻还原
    NOTOPMOST」：真正提到最前，且不会常驻置顶（用户明确不要 always-on-top）。
    """
    f = state['frame']
    if not f or not user32.IsWindow(f) or not user32.IsWindowVisible(f):
        return
    flags = SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOMOVE
    user32.SetWindowPos(f, HWND_TOPMOST, 0, 0, 0, 0, flags)
    user32.SetWindowPos(f, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    sync_child_zorder()


CURSOR_FOR_HIT = {10: 32644, 11: 32644, 12: 32645, 15: 32645,
                  13: 32642, 14: 32643, 16: 32643, 17: 32642}


def _inside_frame(pt, f):
    """屏幕点是否在 frame 外框内，并给出它属于哪条抓边（0=不是抓边）。"""
    r = RECT()
    user32.GetWindowRect(f, ctypes.byref(r))
    if not (r.left <= pt.x < r.right and r.top <= pt.y < r.bottom):
        return False, 0
    return True, rect_edge_hit(r, pt.x, pt.y)


def _owns_point(pt, f):
    """该点当前是否由本程序接收（而不是被别的窗口盖住）。"""
    ch = state['child']
    if user32.WindowFromPoint(pt) in (f, ch):
        return True
    # 圆角/视频区是 alpha=0 的全透明像素，命中的会是底下别的窗口；此时只要
    # 自己就是前台窗口，就认为这一下确实点在我们身上。
    return user32.GetForegroundWindow() in (f, ch)


def mouse_hook_proc(n_code, wparam, lparam):
    """全局低级鼠标钩子：抓边缩放 + 点在魔芋任意区域时请求整体置前。

    视频区是 layered 窗口的 alpha=0 区域，鼠标会穿透到 scrcpy 窗口，而 scrcpy 带
    WS_EX_NOACTIVATE 不会激活 frame —— 这里补上这条激活路径。同时 scrcpy 子窗口
    整块盖住了 frame 的左/右/下三条边，窗口自己根本收不到那几个点的
    WM_NCHITTEST，所以四边四角的缩放也挪到这个钩子里判断和驱动。
    """
    try:
        if n_code >= 0:
            f = state['frame']
            if f and user32.IsWindowVisible(f):
                if state['resizing']:
                    if wparam == 0x0202:  # 松手：收尾，重排视频/重截毛玻璃交给窗口消息
                        end_resize()
                        user32.PostMessageW(f, 0x0232, 0, 0)  # WM_EXITSIZEMOVE
                    elif wparam == 0x0200:  # 拖拽中：钩子驱动缩放
                        do_resize(f)
                    return 1
                if wparam in (0x0201, 0x0204):  # 左/右键按下
                    pt = POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    inside, hit = _inside_frame(pt, f)
                    if inside and hit and _owns_point(pt, f):
                        user32.SetForegroundWindow(f)
                        start_resize(f, hit)
                        return 1
                    if inside:
                        user32.PostMessageW(state['tray'], WM_APP_RAISE, 0, 0)
                elif wparam == 0x0200:  # 悬停在抓边上时给出缩放光标
                    pt = POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    inside, hit = _inside_frame(pt, f)
                    if inside and hit and _owns_point(pt, f):
                        user32.SetCursor(user32.LoadCursorW(None, CURSOR_FOR_HIT[hit]))
    except Exception:
        pass
    return user32.CallNextHookEx(None, n_code, wparam, lparam)


def install_mouse_hook():
    fn = HOOKPROC(mouse_hook_proc)
    _PROC_REFS.append(fn)
    state['hook'] = user32.SetWindowsHookExW(WH_MOUSE_LL, fn, None, 0)
    log('mouse hook=%s' % state['hook'])


def remove_mouse_hook():
    if state['hook']:
        user32.UnhookWindowsHookEx(state['hook'])
        state['hook'] = None


def update_frame(hwnd, force=False):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    W = r.right - r.left
    H = r.bottom - r.top
    if W <= 0 or H <= 0:
        return
    vx, vy, vw, vh = calc_video_rect(W, H)
    state['video_rect'] = (vx, vy, vw, vh)
    # 视频窗口必须最先跟随，绝不能排在操作栏渲染之后：拖动的每一帧里 frame 由系统
    # 立即挪走，视频窗口却要等这一整段（PIL 渲染 ~12ms + 预乘 2ms + 提交）跑完才跟上，
    # 于是肉眼看到操作栏和视频「脱节」。先把它挪到位，再慢慢画操作栏。
    position_scrcpy(vx, vy, vw, vh)
    apply_child_round_region(vw, vh)
    size = (W, H)
    # 纯位移（拖动）时分层窗口的内容与位置无关：尺寸没变就不用重画操作栏，
    # 否则每一次 WM_WINDOWPOSCHANGED 都要白跑十几毫秒，反过来又把视频拖后。
    if not force and state['moving'] and state['drawn_size'] == size:
        return
    key = (r.left, r.top, W, H)
    now = time.time()
    blur_bg = None
    if state['blur'] is not None and (state['blur'][0] == key or state['moving']):
        blur_bg = state['blur'][1]
    elif state['blur'] is not None and now - state['blur_ts'] <= 0.15:
        blur_bg = state['blur'][1]
    else:
        blur_bg = capture_blur(hwnd)
        if blur_bg is not None:
            state['blur'] = (key, blur_bg)
            state['blur_ts'] = now
    img = build_frame_image(W, H, vx, vy, vw, vh, blur_bg)
    raw = premultiply(img).tobytes('raw', 'BGRA')
    draw_layered(hwnd, W, H, raw)
    state['drawn_size'] = size


def in_close_button(hwnd, lparam):
    x = ctypes.c_short(lparam & 0xFFFF).value
    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    W = r.right
    return x >= W - CLOSE_W and y < TOOLBAR_H


def set_cursor_if_close(hwnd):
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    W = r.right
    if pt.x >= W - CLOSE_W and pt.y < TOOLBAR_H:
        user32.SetCursor(user32.LoadCursorW(None, 32649))  # IDC_HAND
        return True
    return False


def track_leave(hwnd):
    tme = TRACKMOUSEEVENT()
    tme.cbSize = ctypes.sizeof(TRACKMOUSEEVENT)
    tme.dwFlags = 0x0002  # TME_LEAVE
    tme.hwndTrack = hwnd
    user32.TrackMouseEvent(ctypes.byref(tme))


def frame_hittest(hwnd, lparam):
    x = ctypes.c_short(lparam & 0xFFFF).value
    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
    pt = POINT(x, y)
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    cx, cy = pt.x, pt.y
    W, H = r.right, r.bottom
    # 边缘判断必须在操作栏之前：否则操作栏上的上/左/右三条边永远只能拖动、不能缩放
    left = cx < RESIZE_EDGE
    right = cx > W - RESIZE_EDGE
    top = cy < RESIZE_EDGE
    bottom = cy > H - RESIZE_EDGE
    if left and top:
        return 13
    if right and top:
        return 14
    if left and bottom:
        return 16
    if right and bottom:
        return 17
    if left:
        return 10
    if right:
        return 11
    if top:
        return 12
    if bottom:
        return 15
    if cy < TOOLBAR_H:
        if cx >= W - CLOSE_W:
            return 1  # HTCLIENT（关闭按钮）
        return 2  # HTCAPTION（拖动）
    return 1


def start_resize(hwnd, hit):
    log('resize start hit=%d' % hit)
    state['resize_log'] = 0
    user32.SetCapture(hwnd)
    state['resizing'] = True
    state['moving'] = True
    state['user_moved'] = True  # 用户亲自改过几何，横竖屏切换不再挪位置
    state['resize_hit'] = hit
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    state['resize_pt'] = (pt.x, pt.y)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    state['resize_rect'] = (r.left, r.top, r.right, r.bottom)
    # 用定时器轮询光标驱动缩放：鼠标压在被 scrcpy 覆盖的视频区上时，frame 拿不到
    # WM_MOUSEMOVE（SetCapture 也救不回来），只有轮询才是稳的。
    user32.SetTimer(hwnd, TIMER_RESIZE, 10, None)


def button_down():
    """左键当前是否仍然按着（0x8000 = 按下）。

    注意：钩子吞掉按下事件后系统不会把按键状态置为按下，这个函数只在
    「按键事件没被吞掉」的路径下可信，缩放收尾仍以钩子的抬键事件为准。
    """
    return bool(user32.GetAsyncKeyState(0x01) & 0x8000)


def do_resize(hwnd):
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    dx = pt.x - state['resize_pt'][0]
    dy = pt.y - state['resize_pt'][1]
    l, t, r, b = state['resize_rect']
    hit = state['resize_hit']
    aspect = state.get('aspect') or 1.0
    if hit in (10, 13, 16):
        l += dx
    if hit in (11, 14, 17):
        r += dx
    if hit in (12, 13, 14):
        t += dy
    if hit in (15, 16, 17):
        b += dy
    min_w, min_h = 2 * FRAME + MIN_VIDEO, TOOLBAR_H + FRAME + MIN_VIDEO
    if r - l < min_w:
        if hit in (10, 13, 16):
            l = r - min_w
        else:
            r = l + min_w
    if b - t < min_h:
        if hit in (12, 13, 14):
            t = b - min_h
        else:
            b = t + min_h
    # 不许缩放到屏幕外：被拖动的那条边是锚点，对边向工作区内收
    wa_h_px, wa_w_px = wa_h(), wa_w()
    max_w = (r if hit in (10, 13, 16) else wa_w_px - l)
    max_h = (b if hit in (12, 13, 14) else wa_h_px - t)
    if hit in (10, 11, 13, 14, 16, 17):  # 横向：宽驱动高，两者一起受限
        w_cap = min(max_w, int((max_h - TOOLBAR_H - FRAME) * aspect) + 2 * FRAME)
        if r - l > w_cap:
            if hit in (10, 13, 16):
                l = r - w_cap
            else:
                r = l + w_cap
    else:  # 纵向：高驱动宽
        h_cap = min(max_h, int((max_w - 2 * FRAME) / aspect) + TOOLBAR_H + FRAME)
        if b - t > h_cap:
            if hit in (12, 13, 14):
                t = b - h_cap
            else:
                b = t + h_cap
    # 需求3：视频区恒等于手机宽高比，缩到极限也不出黑边/拉伸
    l, t, r, b = lock_aspect(l, t, r, b, hit, aspect)
    user32.SetWindowPos(hwnd, None, l, t, r - l, b - t, 0x0004)  # SWP_NOZORDER


def end_resize():
    r = RECT()
    user32.GetWindowRect(state['frame'], ctypes.byref(r)) if state['frame'] else None
    log('resize end hit=%s from=%s to=(%d,%d,%d,%d)'
        % (state.get('resize_hit'), state.get('resize_rect'), r.left, r.top, r.right, r.bottom))
    if state['frame']:
        user32.KillTimer(state['frame'], TIMER_RESIZE)
    state['resizing'] = False
    state['moving'] = False
    user32.ReleaseCapture()


def frame_proc(hwnd, msg, wparam, lparam):
    if msg == 0x0084:  # WM_NCHITTEST
        return frame_hittest(hwnd, lparam)
    if msg == 0x00A1:  # WM_NCLBUTTONDOWN：拖动/缩放前激活窗口
        user32.SetForegroundWindow(hwnd)
        raise_mirror()
        if 10 <= wparam <= 17:
            start_resize(hwnd, wparam)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == 0x0231:  # WM_ENTERSIZEMOVE：系统拖动开始
        state['moving'] = True
        state['user_moved'] = True  # 用户亲手挪过窗口，横竖屏切换不再自动归位
        return 0
    if msg == 0x0006:  # WM_ACTIVATE：激活时整体置前，视频跟着一起到最前
        if wparam & 0xFFFF:
            raise_mirror()
        return 0
    if msg == 0x0232:  # WM_EXITSIZEMOVE：拖动结束，重截毛玻璃背景
        state['moving'] = False
        state['blur'] = None
        update_frame(hwnd)
        return 0
    if msg == 0x0024:  # WM_GETMINMAXINFO
        mmi = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
        mmi.ptMinTrackSize.x = 2 * FRAME + MIN_VIDEO
        mmi.ptMinTrackSize.y = TOOLBAR_H + FRAME + MIN_VIDEO
        return 0
    if msg == 0x0020:  # WM_SETCURSOR
        if state['resizing']:
            return 1
        if set_cursor_if_close(hwnd):
            return 1
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == 0x0113:  # WM_TIMER：缩放期间轮询光标（视频区上的鼠标移动收不到）
        if wparam == TIMER_RESIZE and state['resizing']:
            do_resize(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == 0x0200:  # WM_MOUSEMOVE
        if state['resizing']:
            do_resize(hwnd)
            return 0
        new_hover = in_close_button(hwnd, lparam)
        if new_hover != state['close_hover']:
            state['close_hover'] = new_hover
            if new_hover:
                track_leave(hwnd)
            update_frame(hwnd)
        return 0
    if msg == 0x02A3:  # WM_MOUSELEAVE
        if state['close_hover']:
            state['close_hover'] = False
            update_frame(hwnd)
        return 0
    if msg == 0x0202:  # WM_LBUTTONUP
        if state['resizing']:
            end_resize()
            state['blur'] = None
            update_frame(hwnd)
            return 0
        if in_close_button(hwnd, lparam):
            hide_mirror()
        return 0
    if msg == 0x0216:  # WM_MOVING：系统拖动中，lparam 指向「即将移到」的矩形
        # 必须在 frame 真正被挪走之前把视频窗口放到同一位置，两者才会在同一帧里
        # 一起动；等 WM_WINDOWPOSCHANGED 再补就已经晚一步，看起来就是「脱节」。
        rc = ctypes.cast(lparam, ctypes.POINTER(RECT)).contents
        vx, vy, vw, vh = calc_video_rect(rc.right - rc.left, rc.bottom - rc.top)
        ch = state['child']
        if ch and user32.IsWindow(ch):
            user32.SetWindowPos(ch, hwnd, rc.left + vx, rc.top + vy, vw, vh,
                                SWP_NOACTIVATE)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == 0x0047 or msg == 0x0005:  # WM_WINDOWPOSCHANGED / WM_SIZE
        update_frame(hwnd)
        return 0
    if msg == 0x0002:  # WM_DESTROY
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def hide_mirror():
    f = state['frame']
    if f and user32.IsWindowVisible(f):
        user32.ShowWindow(f, 0)
        if state['child']:
            user32.ShowWindow(state['child'], 0)


def toggle_mirror():
    """托盘左键：保证「点一下=看到窗口」。

    只要窗口当前不在最前（被隐藏 / 被别的窗口盖住 / 没焦点），这一次点击就一律
    按「显示并置前」处理；只有当它已经是最前台窗口时才收起。否则会出现「窗口其
    实还开着、只是被盖住」→ 点一下反而收起、必须再点一次才出来的错觉。
    """
    f = state['frame']
    if not f:
        return
    visible = user32.IsWindowVisible(f)
    fg = user32.GetForegroundWindow()
    on_top = bool(visible) and fg in (f, state['child'])
    if on_top:
        user32.ShowWindow(f, 0)
        if state['child']:
            user32.ShowWindow(state['child'], 0)
        log('toggle: hide (was front)')
        return
    # 显示：先把两个窗口放出来，再重绘、提到最前、抢前台，缺一步都会「看起来没反应」
    user32.ShowWindow(f, 5)
    if state['child']:
        user32.ShowWindow(state['child'], 5)
    update_frame(f)
    raise_mirror()
    force_foreground(f)
    log('toggle: show (visible=%s fg=%s) -> visible=%s front=%s'
        % (visible, fg, user32.IsWindowVisible(f), user32.GetForegroundWindow() == f))


def show_menu(hwnd):
    hmenu = user32.CreatePopupMenu()
    open_text = '隐藏投屏' if mirror_visible() else '显示投屏'
    user32.AppendMenuW(hmenu, 0x0000, ID_TOGGLE, open_text)
    user32.AppendMenuW(hmenu, 0x0800, 0, None)
    user32.AppendMenuW(hmenu, 0x0000, ID_EXIT, '退出')
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)
    user32.TrackPopupMenu(hmenu, 0x0002 | 0x0020, pt.x, pt.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, 0x0000, 0, 0)
    user32.DestroyMenu(hmenu)


def fail(msg):
    if state['error_shown']:
        return
    state['error_shown'] = True
    user32.MessageBoxW(None, msg, APP_NAME, 0x10)


def quit_app():
    if state['quitting']:
        return
    state['quitting'] = True
    remove_mouse_hook()
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = state['tray']
    nid.uID = 1
    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    p = state['proc']
    if p is not None and p.poll() is None:
        try:
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10)
        except Exception:
            pass
    if state['tray']:
        user32.DestroyWindow(state['tray'])
    else:
        user32.PostQuitMessage(0)


def watchdog(hwnd):
    # 每秒兜底：前台是魔芋时整体置前；否则至少保证视频紧贴 frame 下方，
    # 避免其它窗口插进两者之间，把视频区遮掉（只剩操作栏可见）
    f, ch = state['frame'], state['child']
    if f and user32.IsWindowVisible(f) and ch and user32.IsWindow(ch):
        fg = user32.GetForegroundWindow()
        if fg in (f, ch, state['tray']):
            raise_mirror()
        else:
            sync_child_zorder()
    if state['child'] is None:
        t = target_hwnd()
        if t:
            ex = user32.GetWindowLongW(t, -20)
            user32.SetWindowLongW(t, -20, ex | 0x08000000)  # WS_EX_NOACTIVATE，避免抢焦点
            # Windows 11 默认会把顶层窗口四角磨圆（DWMWCP_ROUND=2）。视频窗口一旦被
            # 磨圆，上沿两角就会从操作栏底下露出桌面，形成一道「缝隙」。这里显式关掉
            # 系统圆角（DWMWCP_DONOTROUND=1），形状完全交给 SetWindowRgn 决定：
            # 上沿直角紧贴操作栏，只有下面两角是圆的。
            no_round = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(t, 33, ctypes.byref(no_round), 4)
            state['child'] = t
            r = RECT()
            user32.GetWindowRect(t, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 50 and h > 50:
                state['aspect'] = w / h
                log('aspect from scrcpy = %.4f' % state['aspect'])
            ensure_frame_orientation()
            update_frame(state['frame'])
            log('child ready hwnd=%s' % t)
            return
    else:
        if user32.IsWindow(state['child']):
            # 跟随 scrcpy 窗口实际宽高比（反映手机旋转方向）
            r = RECT()
            user32.GetWindowRect(state['child'], ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 50 and h > 50:
                a = w / h
                if abs(a - state['aspect']) > 0.02:
                    state['aspect'] = a
                    ensure_frame_orientation()
                    update_frame(state['frame'])
                    log('aspect updated %.4f' % a)
            return
        state['child'] = None
    p = state['proc']
    if p is None:
        return
    if p.poll() is not None and not state['quitting']:
        fail('投屏进程已退出。\n请确认手机无线调试已开启，且 config.json 里的 IP 与端口正确。')
        quit_app()
        return
    if time.time() - state['started_at'] > 60:
        fail('投屏窗口 60 秒仍未出现。\n请确认手机端无线调试正常，或重启本程序。')


def tray_proc(hwnd, msg, wparam, lparam):
    if msg == WM_APP_RAISE:
        raise_mirror()
        force_foreground(state['frame'])
        return 0
    if msg == WM_TRAY:
        evt = lparam & 0xFFFF  # 兼容 NOTIFYICON_VERSION_4（事件在 LOWORD，图标 id 在 HIWORD）
        if evt == 0x0205:
            show_menu(hwnd)
        elif evt == 0x0202:
            now = time.time()
            gap = now - state['tray_click_ts']
            if gap >= TRAY_CLICK_GUARD:
                state['tray_click_ts'] = now
                log('tray: click lparam=0x%08x visible=%s gap=%.2fs -> toggle'
                    % (lparam, user32.IsWindowVisible(state['frame'] or 0), gap))
                toggle_mirror()
            else:
                log('tray: click lparam=0x%08x ignored by guard gap=%.2fs'
                    % (lparam, gap))
        return 0
    if msg == 0x0111:
        mid = wparam & 0xFFFF
        if mid == ID_TOGGLE:
            toggle_mirror()
        elif mid == ID_EXIT:
            quit_app()
        return 0
    if msg == 0x0113:
        watchdog(hwnd)
        return 0
    if msg == 0x0002:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def register(cls, proc):
    hinst = kernel32.GetModuleHandleW(None)
    fn = WNDPROC(proc)
    _PROC_REFS.append(fn)
    wc = WNDCLASSW()
    wc.lpfnWndProc = fn
    wc.hInstance = hinst
    wc.hCursor = user32.LoadCursorW(None, 32512)
    wc.lpszClassName = cls
    user32.RegisterClassW(ctypes.byref(wc))
    return hinst


def main():
    kernel32.CreateMutexW(None, False, 'CapyMirrorAppSingleton')
    if ctypes.get_last_error() == 183:
        log('main: mutex exists, abort')
        user32.MessageBoxW(None, '程序已经在运行了，请查看系统托盘。', APP_NAME, 0x40)
        return
    log('main: mutex ok')

    hinst = register(CLASS_TRAY, tray_proc)
    state['tray'] = user32.CreateWindowExW(
        0x00000080, CLASS_TRAY, '', 0, 0, 0, 0, 0, None, None, hinst, None)
    log('main: tray hwnd=%s' % state['tray'])

    icon_size = user32.GetSystemMetrics(49)
    hicon = user32.LoadImageW(None, ICO, 1, icon_size, icon_size, 0x0010)
    if not hicon:
        hicon = user32.LoadIconW(None, 32512)

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = state['tray']
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = hicon
    nid.szTip = APP_NAME
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    log('main: notifyicon=%s hicon=%s' % (hicon, bool(hicon)))
    trc = tray_icon_rect(state['tray'])
    if trc is not None:
        log('main: tray icon rect=(%d,%d)-(%d,%d) %s'
            % (trc.left, trc.top, trc.right, trc.bottom,
               'in-overflow' if trc.top < 100 else 'pinned'))
    else:
        log('main: tray icon rect unknown (not created yet)')
    user32.SetTimer(state['tray'], 1, 1000, None)

    register(CLASS_FRAME, frame_proc)
    fw = CFG['w'] + 2 * FRAME
    fh = CFG['h'] + TOOLBAR_H + FRAME
    state['frame'] = user32.CreateWindowExW(
        0x00080000 | 0x00000080, CLASS_FRAME, '', 0x80000000,
        CFG['x'], CFG['y'], fw, fh, None, None, hinst, None)
    # 外框用分层窗口逐像素 alpha 自己画圆角，关掉系统圆角避免叠加出毛边
    no_round = ctypes.c_int(1)
    dwmapi.DwmSetWindowAttribute(state['frame'], 33, ctypes.byref(no_round), 4)
    move_to_bottom_right()
    update_frame(state['frame'])
    user32.ShowWindow(state['frame'], 5)
    log('main: frame hwnd=%s size=%dx%d' % (state['frame'], fw, fh))
    install_mouse_hook()

    threading.Thread(target=start_scrcpy, daemon=True).start()

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == '__main__':
    main()
