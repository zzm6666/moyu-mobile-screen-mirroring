"""连接手机向导（纯 ctypes Win32，无第三方依赖）。

只在「没有任何在线设备」或用户从托盘菜单点「连接手机…」时弹出，平时不参与启动流程，
所以不影响原有的自动连接逻辑。

窗口跑在独立线程、自带消息循环；adb 调用也在工作线程里做，做完 PostMessage 回窗口。
"""
import ctypes
import ctypes.wintypes as wt
import re
import subprocess
import threading

CREATE_NO_WINDOW = 0x08000000
IP_PORT_RE = re.compile(r'^[\d.]+:\d+$')

user32 = ctypes.WinDLL('user32', use_last_error=True)

WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_SETFONT = 0x0030
WM_GETTEXT = 0x000D
WM_SETTEXT = 0x000C
WM_APP_JOB = 0x8000 + 7          # 工作线程做完通知窗口
BN_CLICKED = 0

WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_TABSTOP = 0x00010000
WS_VSCROLL = 0x00200000
ES_AUTOHSCROLL = 0x0080
ES_NUMBER = 0x2000
BS_PUSHBUTTON = 0x00000000
SS_LEFT = 0x00000000
LBS_NOTIFY = 0x0001
SW_SHOW = 5
GWLP_USERDATA = -21
DEFAULT_GUI_FONT = 17

ID_LIST, ID_REFRESH, ID_USE, ID_IP, ID_CODE, ID_PAIR, ID_STATUS, ID_CONNECT_BTN = range(1001, 1009)

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    """ctypes.wintypes 里没有 WNDCLASSW，自己写一份（字段顺序必须与 Win32 一致）。"""
    _fields_ = [
        ('style', wt.UINT), ('lpfnWndProc', _WNDPROC), ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON),
        ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH),
        ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR),
    ]


_PROC_REFS = []
_WINDOWS = {}          # hwnd -> ctx

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
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ


def _lp(value):
    """把字符串/整数转成能塞进 LPARAM 的整数。"""
    if isinstance(value, str):
        return ctypes.cast(ctypes.c_wchar_p(value), ctypes.c_void_p).value or 0
    if isinstance(value, int):
        return value
    return ctypes.cast(value, ctypes.c_void_p).value or 0


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


def classify_device(serial):
    """带冒号的是无线（adb connect 出来的），否则就是 USB 串号。"""
    return 'wifi' if ':' in (serial or '') else 'usb'


def valid_code(code):
    return re.match(r'^\d{6}$', (code or '').strip()) is not None


def parse_mdns_services(text, ip):
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


def _ip_of(ip_port):
    return (ip_port or '').split(':')[0]


def pair_only(adb, ip_port, code, runner=run_cmd):
    """只做 adb pair；返回 (是否成功, 提示)。"""
    if not normalize_ip_port(ip_port):
        return False, '请填写「IP:配对端口」，例如 192.168.1.5:43847'
    if not valid_code(code):
        return False, '配对码是手机屏幕上的 6 位数字'
    rc, out = runner([adb, 'pair', ip_port, (code or '').strip()])
    if 'Successfully paired' not in (out or ''):
        msg = '配对失败。'
        for line in (out or '').splitlines():
            if line.strip():
                msg += ' ' + line.strip()
                break
        if not (out or '').strip():
            msg += ' 手机上的「无线调试」还开着吗？配对端口每次重开都会变，请重新确认。'
        return False, msg
    return True, ''


def connect_only(adb, ip_port, runner=run_cmd):
    """只做 adb connect；返回 (是否成功, 提示, 串号)。"""
    if not normalize_ip_port(ip_port):
        return False, '请填写「IP:端口」，例如 192.168.1.5:41001', ''
    rc, out = runner([adb, 'connect', ip_port])
    low = (out or '').lower()
    if 'connected' in low:
        return True, '连接成功：' + ip_port, ip_port
    return False, '连接失败：' + (out or '').strip()[:160], ''


def pair_then_connect(adb, ip_port, code, runner=run_cmd):
    """配对 + 找到真正的「连接端口」再连。

    手机无线调试有两个端口：配对弹窗里的**配对端口**和主页面上「IP 地址和端口」的
    **连接端口**，两者通常不同——所以配对成功后不能再拿配对端口去 connect
    （会得到「积极拒绝 10061」）。这里按三步找连接方式：
      1. 配对成功后先看 `adb devices`（mDNS 常常直接出现可用设备）；
      2. 不行再查 `adb mdns services` 拿该 IP 的连接端口，然后 connect；
      3. 都不行就把话说明白，让用户把「IP 地址和端口」填进来点「连接」。
    """
    ok, msg = pair_only(adb, ip_port, code, runner)
    if not ok:
        return False, msg, ''
    devs = list_online(adb, runner)
    if devs:
        return True, '配对并连接成功：' + devs[0], devs[0]
    serial = parse_mdns_services(runner([adb, 'mdns', 'services'])[1], _ip_of(ip_port))
    if serial:
        ok2, msg2, s2 = connect_only(adb, serial, runner)
        if ok2:
            return True, '配对并连接成功：' + s2, s2
    return False, ('配对成功，但还差连接端口：请把手机「无线调试」页面上的'
                   '「IP 地址和端口」（不是配对端口）填到上面，再点「连接」。'), ''


def list_online(adb, runner=run_cmd):
    """当前在线设备（状态 device 才算）。"""
    rc, out = runner([adb, 'devices'])
    devs = []
    for line in (out or '').splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == 'device':
            devs.append(parts[0])
    return devs


def _text(hwnd):
    """读控件文字（EDIT）。"""
    n = int(user32.GetWindowTextLengthW(hwnd) or 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _status(ctx, text):
    user32.SetWindowTextW(ctx['status'], text)


def _fill_list(ctx, devs):
    lb = ctx['list']
    user32.SendMessageW(lb, 0x0184, 0, None)        # LB_RESETCONTENT
    for d in devs:
        user32.SendMessageW(lb, 0x0180, 0, _lp(d))  # LB_ADDSTRING
    if devs:
        user32.SendMessageW(lb, 0x0186, 0, None)    # LB_SETCURSEL(0)


def _refresh(ctx):
    devs = list_online(ctx['adb'])
    _fill_list(ctx, devs)
    if devs:
        _status(ctx, '找到 %d 台设备，选中后点「用这台设备」。' % len(devs))
    else:
        _status(ctx, '没找到设备：插好数据线并在手机上允许 USB 调试，或改用下面的无线配对。')
    if ctx['log']:
        ctx['log']('wizard: devices=%s' % (devs,))


def _worker_job(ctx, kind, payload):
    """在工作线程里跑 adb，结果放到 ctx 里再 PostMessage 回窗口。"""
    try:
        if kind == 'refresh':
            devices = list_online(ctx['adb'])
            ctx['job'] = ('refresh', devices, '')
        elif kind == 'pair':
            ok, msg, serial = pair_then_connect(ctx['adb'], payload[0], payload[1])
            ctx['job'] = ('pair', (ok, msg, serial), '')
        elif kind == 'connect':
            ok, msg, serial = connect_only(ctx['adb'], payload)
            ctx['job'] = ('connect', (ok, msg, serial), '')
    except Exception as exc:
        ctx['job'] = (kind, '', str(exc))
    user32.PostMessageW(ctx['hwnd'], WM_APP_JOB, 0, 0)


def _on_job_done(ctx):
    job = ctx.get('job')
    if not job:
        return
    ctx['job'] = None
    kind = job[0]
    if kind == 'refresh':
        devs = job[1] or []
        _fill_list(ctx, devs)
        if devs:
            _status(ctx, '找到 %d 台设备，选中后点「用这台设备」。' % len(devs))
        else:
            _status(ctx, '没找到设备：插好数据线并在手机上允许 USB 调试，或改用下面的无线配对。')
        if ctx['log']:
            ctx['log']('wizard: devices=%s' % (devs,))
    elif kind in ('pair', 'connect'):
        ok, msg, serial = job[1] if isinstance(job[1], tuple) else (False, str(job[1]), '')
        if ctx['log']:
            ctx['log']('wizard: %s -> ok=%s msg=%s' % (kind, ok, msg))
        if ok:
            _status(ctx, '已连接：' + serial)
            _finish(ctx, serial)
        else:
            _status(ctx, msg)
    ctx['busy'] = False


def _notify_close(ctx):
    """窗口没了但没连上（用户点 X）：通知外面把「向导已开」的状态清掉，否则再也弹不出来。"""
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
    hwnd = ctx['hwnd']
    ctx['closed'] = True
    if ctx['log']:
        ctx['log']('wizard: done serial=%s' % serial)
    user32.DestroyWindow(hwnd)
    if ctx.get('on_done'):
        try:
            ctx['on_done'](serial)
        except Exception as exc:
            if ctx['log']:
                ctx['log']('wizard: on_done 失败 %s' % exc)


def _wndproc(hwnd, msg, wparam, lparam):
    ctx = _WINDOWS.get(hwnd)
    if msg == WM_COMMAND and ctx:
        cid = wparam & 0xFFFF
        if cid == ID_REFRESH:
            if not ctx['busy']:
                ctx['busy'] = True
                _status(ctx, '正在查找设备…')
                threading.Thread(target=_worker_job, args=(ctx, 'refresh', None),
                                 daemon=True).start()
            return 0
        if cid == ID_USE:
            idx = int(user32.SendMessageW(ctx['list'], 0x0188, 0, None) or 0)   # LB_GETCURSEL
            if idx < 0:
                _status(ctx, '先在列表里选一台设备。')
                return 0
            n = int(user32.SendMessageW(ctx['list'], 0x0189, idx, None) or 0) + 1  # LB_GETTEXTLEN
            buf = ctypes.create_unicode_buffer(max(n, 1))
            user32.SendMessageW(ctx['list'], 0x018A, idx,               # LB_GETTEXT
                                ctypes.c_void_p(ctypes.addressof(buf)))
            serial = buf.value
            if serial:
                _finish(ctx, serial)
            return 0
        if cid == ID_PAIR:
            if ctx['busy']:
                return 0
            ip_port = normalize_ip_port(_text(ctx['ip']))
            if not ip_port:
                _status(ctx, '「IP:端口」要带端口号，例如 192.168.137.198:43847')
                return 0
            if not valid_code(_text(ctx['code'])):
                _status(ctx, '配对码是手机屏幕上的 6 位数字。')
                return 0
            ctx['busy'] = True
            _status(ctx, '正在配对…')
            threading.Thread(target=_worker_job,
                             args=(ctx, 'pair', (ip_port, _text(ctx['code']))),
                             daemon=True).start()
            return 0
        if cid == ID_CONNECT_BTN:
            if ctx['busy']:
                return 0
            ip_port = normalize_ip_port(_text(ctx['ip']))
            if not ip_port:
                _status(ctx, '「IP:端口」要带端口号，例如 192.168.137.198:43847')
                return 0
            ctx['busy'] = True
            _status(ctx, '正在连接…')
            threading.Thread(target=_worker_job, args=(ctx, 'connect', ip_port),
                             daemon=True).start()
            return 0
        return 0
    if msg == WM_APP_JOB and ctx:
        _on_job_done(ctx)
        return 0
    if msg == WM_CLOSE and ctx:
        if ctx['log']:
            ctx['log']('wizard: 用户关闭')
        _notify_close(ctx)
        user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY and ctx:
        _notify_close(ctx)
        _WINDOWS.pop(hwnd, None)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _make(ctx, cls, text, style, x, y, w, h, parent=None, cid=0):
    hwnd = user32.CreateWindowExW(0, cls, text, style, x, y, w, h,
                                  parent or ctx['hwnd'], cid,
                                  ctypes.windll.kernel32.GetModuleHandleW(None), None)
    if hwnd and ctx.get('font'):
        user32.SendMessageW(hwnd, WM_SETFONT, ctx['font'], 1)
    return hwnd


def show_wizard(adb, on_done, on_log=None, on_close=None):
    """在独立线程里弹出向导；on_done(serial) 在向导线程里被调用。"""
    def _log(msg):
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    def _worker():
        try:
            _build_and_run(adb, on_done, on_log, on_close)
        except Exception as exc:                 # 线程里没人接异常，一定要写日志
            import traceback
            _log('wizard: 异常 %s' % exc)
            _log('wizard: ' + traceback.format_exc().replace('\n', ' | '))

    def _build_and_run(adb, on_done, on_log, on_close):
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = _WNDPROC(_wndproc)
        _PROC_REFS.append(wc.lpfnWndProc)
        wc.hInstance = hinst
        wc.lpszClassName = 'MoyuConnectWizard'
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
        wc.hbrBackground = wt.HBRUSH(16)            # COLOR_BTNFACE + 1
        try:
            user32.RegisterClassW(ctypes.byref(wc))
        except Exception:
            pass
        hwnd = user32.CreateWindowExW(0, 'MoyuConnectWizard', '魔芋 · 连接手机',
                                      WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
                                      200, 160, 470, 400, None, None, hinst, None)
        if not hwnd:
            if on_log:
                on_log('wizard: 创建窗口失败 err=%s' % ctypes.get_last_error())
            return
        ctx = {'hwnd': hwnd, 'adb': adb, 'on_done': on_done, 'log': on_log,
               'on_close': on_close, 'closed': False, 'job': None, 'busy': False}
        ctx['font'] = gdi32.GetStockObject(DEFAULT_GUI_FONT)
        _WINDOWS[hwnd] = ctx
        st = WS_CHILD | WS_VISIBLE
        ctx['list'] = _make(ctx, 'LISTBOX', '', st | WS_BORDER | WS_VSCROLL | LBS_NOTIFY,
                            16, 44, 430, 96, cid=ID_LIST)
        _make(ctx, 'STATIC', '方法一：用数据线连接手机（USB）', st, 16, 18, 430, 20)
        _make(ctx, 'BUTTON', '刷新设备列表', st | BS_PUSHBUTTON | WS_TABSTOP,
              16, 148, 120, 26, cid=ID_REFRESH)
        _make(ctx, 'BUTTON', '用这台设备', st | BS_PUSHBUTTON | WS_TABSTOP,
              146, 148, 120, 26, cid=ID_USE)
        _make(ctx, 'STATIC', '方法二：无线调试配对（手机：开发者选项 → 无线调试）',
              st, 16, 190, 430, 20)
        _make(ctx, 'STATIC', 'IP:端口', st, 16, 220, 60, 20)
        ctx['ip'] = _make(ctx, 'EDIT', '192.168.137.198:',
                          st | WS_BORDER | ES_AUTOHSCROLL | WS_TABSTOP,
                          80, 216, 190, 24, cid=ID_IP)
        _make(ctx, 'STATIC', '配对码', st, 16, 252, 60, 20)
        ctx['code'] = _make(ctx, 'EDIT', '',
                            st | WS_BORDER | ES_NUMBER | WS_TABSTOP,
                            80, 248, 90, 24, cid=ID_CODE)
        _make(ctx, 'BUTTON', '配对', st | BS_PUSHBUTTON | WS_TABSTOP,
              180, 246, 80, 26, cid=ID_PAIR)
        _make(ctx, 'BUTTON', '连接', st | BS_PUSHBUTTON | WS_TABSTOP,
              270, 246, 80, 26, cid=ID_CONNECT_BTN)
        ctx['status'] = _make(ctx, 'STATIC',
                              '点「刷新设备列表」看看有没有已授权的手机。',
                              st, 16, 288, 430, 60, cid=ID_STATUS)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)
        _refresh(ctx)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    t = threading.Thread(target=_worker, name='connect-wizard', daemon=True)
    t.start()
    return t
