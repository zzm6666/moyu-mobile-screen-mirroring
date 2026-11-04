# -*- coding: utf-8 -*-
"""投屏小窗的悬浮关闭按钮（零依赖，纯 ctypes 调 Win32）。

行为：
  - 按钮贴在投屏窗口右上角，平时隐藏，鼠标移到角落才浮现
  - 左键：隐藏投屏窗口（按钮变 ▸ 并常驻，方便恢复），再点一次恢复
  - 每 100ms 强制置顶一次，防止被同为 topmost 的投屏窗口压下去
  - 目标窗口消失 20 秒后自行退出

用法：
  python overlay_close.py [窗口标题]
"""

import ctypes
import ctypes.wintypes as wt
import sys

TITLE = sys.argv[1] if len(sys.argv) > 1 else 'phone-corner'
SIZE = 28
HOVER = 44
CLASS_NAME = 'CapyMirrorCloseBtn'
HWND_TOPMOST = ctypes.c_void_p(-1)

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

user32.SetProcessDPIAware()

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wt.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wt.HINSTANCE),
        ('hIcon', wt.HICON),
        ('hCursor', wt.HANDLE),
        ('hbrBackground', wt.HBRUSH),
        ('lpszMenuName', wt.LPCWSTR),
        ('lpszClassName', wt.LPCWSTR),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ('left', ctypes.c_long),
        ('top', ctypes.c_long),
        ('right', ctypes.c_long),
        ('bottom', ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ('hdc', wt.HDC),
        ('fErase', wt.BOOL),
        ('rcPaint', RECT),
        ('fRestore', wt.BOOL),
        ('fIncUpdate', wt.BOOL),
        ('rgbReserved', ctypes.c_byte * 32),
    ]


# 64 位下必须显式声明签名，否则句柄会被截断
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wt.HANDLE
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(RECT), wt.HBRUSH]
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(RECT), wt.UINT]
user32.DrawTextW.restype = ctypes.c_int

gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                              wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]

state = {'hidden': False, 'hwnd': None, 'shown': False, 'miss': 0}


def target_hwnd():
    return user32.FindWindowW(None, TITLE)


def paint(hwnd):
    ps = PAINTSTRUCT()
    hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
    brush = gdi32.CreateSolidBrush(0x00282828)
    user32.FillRect(hdc, ctypes.byref(ps.rcPaint), brush)
    gdi32.DeleteObject(brush)
    gdi32.SetBkMode(hdc, 1)
    gdi32.SetTextColor(hdc, 0x00EDEDED)
    font = gdi32.CreateFontW(18, 0, 0, 0, 500, 0, 0, 0, 1, 0, 0, 0, 0, 'Segoe UI')
    old = gdi32.SelectObject(hdc, font)
    rc = RECT(0, 0, SIZE, SIZE)
    text = '\u25b8' if state['hidden'] else '\u00d7'
    user32.DrawTextW(hdc, text, -1, ctypes.byref(rc), 0x0001 | 0x0004 | 0x0020)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(font)
    user32.EndPaint(hwnd, ctypes.byref(ps))


def set_shown(hwnd, want):
    if want == state['shown']:
        return
    user32.ShowWindow(hwnd, 8 if want else 0)
    state['shown'] = want
    if want:
        user32.InvalidateRect(hwnd, None, True)


def tick(hwnd):
    t = target_hwnd()
    if not t:
        state['miss'] += 1
        if state['miss'] > 200:
            user32.DestroyWindow(hwnd)
        return
    state['miss'] = 0

    r = RECT()
    user32.GetWindowRect(t, ctypes.byref(r))
    bx = r.right - SIZE - 4
    by = r.top + 4

    # 每拍强制置顶，压过同为 topmost 的投屏窗口
    user32.SetWindowPos(hwnd, HWND_TOPMOST, bx, by, SIZE, SIZE, 0x0010)

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    inside = (r.right - HOVER <= pt.x <= r.right) and (r.top <= pt.y <= r.top + HOVER)

    # 投屏被隐藏时按钮必须常驻，否则没法恢复
    set_shown(hwnd, state['hidden'] or inside)


def toggle():
    t = target_hwnd()
    if not t:
        return
    if state['hidden']:
        user32.ShowWindow(t, 5)
        state['hidden'] = False
    else:
        user32.ShowWindow(t, 0)
        state['hidden'] = True
    user32.InvalidateRect(state['hwnd'], None, True)


def wndproc(hwnd, msg, wparam, lparam):
    if msg == 0x000F:          # WM_PAINT
        paint(hwnd)
        return 0
    if msg == 0x0014:          # WM_ERASEBKGND
        return 1
    if msg == 0x0202:          # WM_LBUTTONUP
        toggle()
        return 0
    if msg == 0x0113:          # WM_TIMER
        tick(hwnd)
        return 0
    if msg == 0x0002:          # WM_DESTROY
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def main():
    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = WNDPROC(wndproc)
    wc.hInstance = hinst
    wc.hCursor = user32.LoadCursorW(None, 32512)
    wc.hbrBackground = None
    wc.lpszClassName = CLASS_NAME
    user32.RegisterClassW(ctypes.byref(wc))

    # 不带 WS_VISIBLE：初始隐藏，等鼠标靠近再显示
    hwnd = user32.CreateWindowExW(
        0x00000008 | 0x00000080,   # WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        CLASS_NAME, '', 0x80000000,
        0, 0, SIZE, SIZE, None, None, hinst, None)
    state['hwnd'] = hwnd

    user32.SetTimer(hwnd, 1, 100, None)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == '__main__':
    main()
