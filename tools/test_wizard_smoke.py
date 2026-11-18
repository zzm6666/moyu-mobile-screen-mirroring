# -*- coding: utf-8 -*-
"""向导 UI 冒烟测试：真窗口、假 adb（不碰真机），验证窗口能开、能画、能关。

运行：.venv\\Scripts\\python.exe -X utf8 -m unittest test_wizard_smoke -v
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import connect_ui

DEVICES = ('List of devices attached\n'
           'adb-bfd7e543-MoDo9u._adb-tls-connect._tcp device '
           'product:PJZ110 model:PJZ110 device:OP5D0DL1 transport_id:4\n')
MDNS = ('List of discovered mdns services\n'
        'adb-bfd7e543-MoDo9u\t_adb-tls-connect._tcp.\t192.168.137.198:41001\n')
PROPS = '\n一加 13\nPJZ110\n'


def fake_runner(cmd, timeout=25):
    joined = ' '.join(cmd)
    if 'devices' in joined:
        return 0, DEVICES
    if 'mdns' in joined:
        return 0, MDNS
    if 'getprop' in joined:
        return 0, PROPS
    if 'connect' in joined:
        return 0, 'connected to 192.168.137.198:41001'
    return 0, ''


DEVICES_MIX = ('List of devices attached\n'
               'OLDPHONE123 device product:PKG110 model:PKG110 transport_id:2\n'
               'adb-bfd7e543-MoDo9u._adb-tls-connect._tcp device '
               'product:PJZ110 model:PJZ110 device:OP5D0DL1 transport_id:4\n')
MDNS_MIX = ('List of discovered mdns services\n'
            'adb-bfd7e543-MoDo9u\t_adb-tls-connect._tcp.\t192.168.137.198:41001\n'
            'adb-aaaa1111-NewPhone\t_adb-tls-connect._tcp.\t192.168.137.88:41234\n')


def fake_runner_mixed(cmd, timeout=25):
    joined = ' '.join(cmd)
    if 'devices' in joined:
        return 0, DEVICES_MIX
    if 'mdns' in joined:
        return 0, MDNS_MIX
    if 'getprop' in joined:
        return 0, PROPS
    if 'connect' in joined:
        return 0, 'connected to 192.168.137.88:41234'
    return 0, ''


def _own_window(on_done_cb, timeout=10):
    """找「本测试自己的」向导 hwnd：按传入的 on_done 回调对象认窗口。

    不能拿 _WINDOWS 里第一个 hwnd——同进程上一个测试的窗口可能还没销毁完，
    残留 hwnd 已失效，点它/查它全是假象。
    """
    for _ in range(int(timeout / 0.1)):
        for h, c in list(connect_ui._WINDOWS.items()):
            if c.get('on_done') is on_done_cb:
                return h
        time.sleep(0.1)
    return None


class WizardSmokeTest(unittest.TestCase):
    def test_hover_only_on_connect_button_not_row(self):
        """悬停「连接」按钮 → 按钮变色、设备行不变色；悬停设备行其余位置 → 无任何 hover。"""
        closed = threading.Event()
        done = threading.Event()
        done_cb = done.set   # 绑定方法每次取属性都是新对象，先存引用再传+认窗口
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         on_close=closed.set, runner=fake_runner_mixed))
        t.start()
        hwnd = _own_window(done_cb)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        u = connect_ui.user32
        g = connect_ui.gdi32
        ctx = connect_ui._WINDOWS[hwnd]
        for _ in range(80):
            if ctx['rows'] and u.SendMessageW(hwnd, 0x007F, 1, 0):
                break
            time.sleep(0.1)
        rows = ctx['visible_rows']
        j = next((i for i, r in enumerate(rows) if r['state'] == 'found'), None)
        self.assertIsNotNone(j, '假 runner 应刷出带连接按钮的新发现设备')

        def pixel(cx, cy):
            """PrintWindow 整窗渲染后取像素（窗口被挡也准），再换算客户区坐标。"""
            rc = wt.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            pt = wt.POINT(0, 0)
            u.ClientToScreen(hwnd, ctypes.byref(pt))
            ox, oy = pt.x - rc.left, pt.y - rc.top
            hdc = u.GetDC(hwnd)
            mem = g.CreateCompatibleDC(hdc)
            bmp = g.CreateCompatibleBitmap(hdc, w, h)
            old = g.SelectObject(mem, bmp)
            u.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
            u.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT

            class BIH(ctypes.Structure):
                _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                            ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                            ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                            ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                            ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                            ('biClrImportant', ctypes.c_uint32)]

            bi = BIH()
            bi.biSize = ctypes.sizeof(BIH)
            bi.biWidth, bi.biHeight = w, -h
            bi.biPlanes, bi.biBitCount = 1, 32
            buf = ctypes.create_string_buffer(w * h * 4)
            g.GetDIBits.argtypes = [wt.HDC, wt.HGDIOBJ, ctypes.c_uint, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
            g.SelectObject(mem, old)
            g.DeleteObject(bmp)
            g.DeleteDC(mem)
            u.ReleaseDC(hwnd, hdc)
            o = ((cy + oy) * w + (cx + ox)) * 4
            return (buf.raw[o + 2], buf.raw[o + 1], buf.raw[o])

        bx, by, bw, bh = ctx['hotspots'][connect_ui.ID_ROW_BASE + j]
        row_y = connect_ui.LIST_Y + j * connect_ui.ROW_H
        row_px = (connect_ui.PAD + 100, row_y + connect_ui.ROW_H - 8)  # 行内空白，非按钮
        btn_px = (bx + bw // 2, by + bh - 4)                           # 按钮内靠下，避开文字
        k = next((i for i, r in enumerate(rows) if r['state'] == 'offline'), None)
        off_px = None
        if k is not None:
            obx, oby, obw, obh = ctx['hotspots'][connect_ui.ID_ROW_BASE + k]
            off_px = (obx + obw // 2, oby + obh - 4)
        base_btn, base_row = pixel(*btn_px), pixel(*row_px)
        self.assertEqual(base_btn, connect_ui.C_PRIMARY, '按钮常态应为主色')
        if off_px:
            self.assertEqual(pixel(*off_px), connect_ui.C_SURFACE, '离线按钮常态应为白底描边')
        self.assertEqual(base_row, connect_ui.C_SURFACE, '设备行常态应为卡片白')
        # 无头环境真实光标不在窗口上，TrackMouseEvent 会立刻补发 WM_MOUSELEAVE
        # 清掉 hover；这里只测悬停绘制逻辑，屏蔽系统鼠标跟踪。
        orig_tme = connect_ui.user32.TrackMouseEvent
        connect_ui.user32.TrackMouseEvent = lambda *a: 1
        try:
            # 悬停设备行其余位置：不产生 hover，行不变色（先移到行上，再移到按钮）
            lp = row_px[0] | (row_px[1] << 16)
            u.PostMessageW(hwnd, 0x0200, 0, lp)
            time.sleep(0.4)
            self.assertEqual(ctx['hover'], 0, '设备行本身不应有 hover')
            self.assertEqual(pixel(*row_px), connect_ui.C_SURFACE, '悬停设备行不应变色')
            self.assertEqual(pixel(*btn_px), connect_ui.C_PRIMARY, '按钮未悬停应保持主色')
            # 悬停按钮：参照刷新按钮的 hover——浅绿底+主色文字，行保持白
            lp = btn_px[0] | (btn_px[1] << 16)
            u.PostMessageW(hwnd, 0x0200, 0, lp)
            time.sleep(0.4)
            self.assertEqual(ctx['hover'], connect_ui.ID_ROW_BASE + j, '按钮应响应悬停')
            self.assertEqual(pixel(*btn_px), connect_ui.C_PRIMARY_LT,
                             '悬停时按钮应像刷新按钮一样变浅绿底')
            self.assertEqual(pixel(*row_px), connect_ui.C_SURFACE,
                             '悬停按钮时设备行不应变色（只有按钮 hover）')
            if off_px:
                lp = off_px[0] | (off_px[1] << 16)
                u.PostMessageW(hwnd, 0x0200, 0, lp)
                time.sleep(0.4)
                self.assertEqual(ctx['hover'], connect_ui.ID_ROW_BASE + k)
                self.assertEqual(pixel(*off_px), connect_ui.C_PRIMARY_LT,
                                 '离线按钮悬停也应变浅绿底')
        finally:
            connect_ui.user32.TrackMouseEvent = orig_tme
        u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self.assertTrue(closed.wait(5), '窗口没关闭')
        t.join(5)
        self.assertFalse(t.is_alive(), '向导线程没退出')

    def test_connected_row_not_clickable_and_button_connects(self):
        """已连接的设备行点了不能有任何反应；连接要走未连接设备右侧的「连接」按钮。

        用户 m02243 反馈：点设备项（还是已连接的设备）又触发了一次连接，
        结果开出第二个「裸」投屏窗口。所以行本身不再是热点，只有「连接」按钮是。
        """
        done = threading.Event()
        closed = threading.Event()
        result = {}

        def done_cb(s, n=''):
            result.update(serial=s, name=n)
            done.set()

        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb',
                                         on_done=done_cb,
                                         on_close=closed.set,
                                         runner=fake_runner_mixed))
        t.start()
        hwnd = _own_window(done_cb)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        ctx = connect_ui._WINDOWS[hwnd]
        # 等首次刷新完成，拿到设备行
        for _ in range(80):
            if ctx['rows']:
                break
            time.sleep(0.1)
        self.assertTrue(ctx['rows'], '假 runner 没刷出设备')
        rows = ctx['rows']
        vis = ctx['visible_rows']
        online = [i for i, r in enumerate(vis) if r['state'] == 'online']
        self.assertTrue(online, '假数据里应该有已连接的设备')
        # 已连接（在线）的设备不显示「连接」按钮，整行也不再是热点
        for i in online:
            self.assertNotIn(connect_ui.ID_ROW_BASE + i, ctx['hotspots'],
                             '在线设备不应有连接按钮')
        found = [j for j, r in enumerate(vis) if r['state'] == 'found']
        self.assertTrue(found, '假数据里应该有一台「新发现」的设备')
        for j in found:
            self.assertIn(connect_ui.ID_ROW_BASE + j, ctx['hotspots'],
                          '未连接设备应保留连接按钮')
        # 1) 点已连接的设备行：不能触发连接（没有 toast、没有 on_done）
        i = online[0]
        y = connect_ui.LIST_Y + i * connect_ui.ROW_H
        lp = (connect_ui.PAD + 100) | ((y + 20) << 16)
        connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)  # WM_LBUTTONDOWN
        connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)  # WM_LBUTTONUP
        time.sleep(0.5)
        self.assertEqual(ctx.get('toast'), '', '点已连接的设备行不该发起连接')
        self.assertFalse(done.is_set(), '点已连接的设备行不该回调 on_done')
        self.assertTrue(connect_ui.user32.IsWindow(hwnd), '窗口不该关闭')
        # 2) 点「新发现」设备右侧的连接按钮：连上 → toast → 1 秒后自动关窗
        j = found[0]
        bx, by, bw, bh = ctx['hotspots'][connect_ui.ID_ROW_BASE + j]
        lp = (bx + bw // 2) | ((by + bh // 2) << 16)
        connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
        connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)
        # toast 出现后窗口必须还活着（不能立刻销毁）
        saw_toast = False
        for _ in range(60):
            if ctx.get('toast'):
                saw_toast = True
                break
            time.sleep(0.05)
        self.assertTrue(saw_toast, '连接成功后没有显示 toast')
        self.assertEqual(ctx['toast'], '连接成功')
        self.assertTrue(connect_ui.user32.IsWindow(hwnd), 'toast 期间窗口不应消失')
        self.assertFalse(done.is_set(), 'toast 还没满 1 秒就回调了 on_done')
        # 1 秒后窗口自动关闭、on_done 带上串号和名字
        self.assertTrue(done.wait(5), 'toast 后窗口没有自动关闭')
        self.assertEqual(result['serial'], vis[j]['serial'])
        self.assertEqual(result['name'], vis[j]['name'])
        for _ in range(50):   # WM_DESTROY 是同步销毁，但消息循环收尾略滞后
            if not connect_ui.user32.IsWindow(hwnd):
                break
            time.sleep(0.05)
        self.assertFalse(connect_ui.user32.IsWindow(hwnd), '窗口应已销毁')
        t.join(5)
        self.assertFalse(t.is_alive(), '向导线程没退出')

    def test_window_opens_paints_and_closes(self):
        done = threading.Event()
        closed = threading.Event()
        result = {}

        def on_done(serial, name=''):
            result['serial'] = serial
            result['name'] = name
            done.set()

        def on_close():
            closed.set()

        logs = []
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=on_done,
                                         on_log=logs.append, on_close=on_close,
                                         known=[{'serial': '192.168.137.198:39001',
                                                 'name': '一加 13',
                                                 'addr': '192.168.137.198:39001',
                                                 'last_seen': 1}],
                                         runner=fake_runner,
                                         icon_path=os.path.join(
                                             os.path.dirname(os.path.abspath(__file__)),
                                             'app.ico')))
        t.start()
        # 认「本测试自己的」窗口（on_done 是本测试的局部函数，对象唯一），
        # 避免抓到同进程上一个测试尚未销毁完的残留 hwnd。
        hwnd = _own_window(on_done)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        time.sleep(1.5)  # 让它完成首次刷新与绘制
        self.assertTrue(connect_ui.user32.IsWindowVisible(hwnd), '窗口不可见')
        # 窗口图标必须是魔芋应用图标（标题栏/任务栏），不能是系统默认图标
        WM_GETICON = 0x007F
        hicon = connect_ui.user32.SendMessageW(hwnd, WM_GETICON, 1, 0)  # ICON_SMALL
        self.assertTrue(hicon, '向导窗口没有设置应用图标')
        # 悬停重绘必须走离屏双缓冲（BitBlt 一次上屏），不能直画屏幕 DC——
        # 直画会先整窗刷背景再补文字，鼠标划过 tab/刷新时内容区闪烁跳动。
        blits = []
        orig_blt = connect_ui.gdi32.BitBlt

        def spy_blt(*a):
            blits.append(1)
            return orig_blt(*a)

        connect_ui.gdi32.BitBlt = spy_blt
        try:
            lp = 74 | (73 << 16)   # 无线 tab 中心 (74,73)
            connect_ui.user32.PostMessageW(hwnd, 0x0200, 0, lp)  # WM_MOUSEMOVE
            time.sleep(0.6)
            self.assertTrue(blits, '悬停重绘没有经过离屏缓冲 BitBlt（会闪烁）')
        finally:
            connect_ui.gdi32.BitBlt = orig_blt
        # IP 和端口必须是同一行的两个输入框，中间画「:」，用户不用输冒号
        edits = connect_ui._WINDOWS[hwnd]['edits']
        self.assertIn(connect_ui.ID_IP, edits, '缺少 IP 输入框')
        self.assertIn(connect_ui.ID_PORT, edits, '缺少端口输入框')
        self.assertIn(connect_ui.ID_CODE, edits, '缺少配对码输入框')
        r_ip, r_port = wt.RECT(), wt.RECT()
        connect_ui.user32.GetWindowRect(edits[connect_ui.ID_IP], ctypes.byref(r_ip))
        connect_ui.user32.GetWindowRect(edits[connect_ui.ID_PORT], ctypes.byref(r_port))
        self.assertEqual(r_ip.top, r_port.top, 'IP 和端口不在同一行')
        self.assertLess(r_ip.right, r_port.left, '端口框应在 IP 框右侧')
        self.assertEqual(r_port.left - r_ip.right, 2 * connect_ui.COLON_GAP,
                         '冒号两侧间隙应一致且为 COLON_GAP')
        rc_ip = wt.RECT()
        connect_ui.user32.GetClientRect(edits[connect_ui.ID_IP], ctypes.byref(rc_ip))
        self.assertEqual(rc_ip.bottom, connect_ui.EDIT_H,
                         '框高应让原生文字天然垂直居中（20）')
        # 三个输入框都要有 placeholder 提示（IP 举例 / 端口几位 / 配对码几位）
        cues = connect_ui._WINDOWS[hwnd]['cues']
        for eid, frag in ((connect_ui.ID_IP, '192.168'),
                          (connect_ui.ID_PORT, '端口'),
                          (connect_ui.ID_CODE, '配对码')):
            self.assertIn(frag, cues.get(eid, ''), '输入框 %d 没有 placeholder' % eid)
        # 端到端：只填两个框（不输冒号）+ 配对码，点「连接手机」必须拼出 IP:端口
        fake_cmds = []
        orig_runner = connect_ui._WINDOWS[hwnd]['runner']

        def spy_runner(cmd, timeout=25):
            fake_cmds.append(list(cmd))
            j = ' '.join(cmd)
            if ' pair ' in j:
                return 0, 'Successfully paired to 192.168.1.5:43847'
            return orig_runner(cmd, timeout)

        connect_ui._WINDOWS[hwnd]['runner'] = spy_runner
        WM_SETTEXT = 0x000C
        connect_ui.user32.SendMessageW(edits[connect_ui.ID_IP], WM_SETTEXT, 0,
                                       ctypes.c_wchar_p('192.168.1.5'))
        connect_ui.user32.SendMessageW(edits[connect_ui.ID_PORT], WM_SETTEXT, 0,
                                       ctypes.c_wchar_p('43847'))
        connect_ui.user32.SendMessageW(edits[connect_ui.ID_CODE], WM_SETTEXT, 0,
                                       ctypes.c_wchar_p('042917'))
        connect_ui.user32.PostMessageW(hwnd, 0x0111, connect_ui.ID_GO, 0)  # WM_COMMAND
        for _ in range(50):
            if any('pair' in c for c in fake_cmds):
                break
            time.sleep(0.1)
        pair_cmd = next((c for c in fake_cmds if 'pair' in c), None)
        self.assertIsNotNone(pair_cmd, '点连接后没有发起 adb pair，日志=%r' % logs)
        self.assertIn('192.168.1.5:43847', pair_cmd,
                      '两个输入框没有拼成 IP:端口：%r' % pair_cmd)
        self.assertIn('042917', pair_cmd)
        connect_ui._WINDOWS[hwnd]['runner'] = orig_runner
        # 先清空 pair 流程留下的输入，再验证 placeholder 行为：聚焦隐藏、失焦且空时恢复。
        # 无头环境里跨线程 SetFocus/AttachThreadInput 都拿不到真实焦点，
        # 直接投递 WM_SETFOCUS/WM_KILLFOCUS 走 EDIT 子类化的同一条代码路径
        ip_eh = edits[connect_ui.ID_IP]
        port_eh = edits[connect_ui.ID_PORT]
        for eh in (ip_eh, port_eh, edits[connect_ui.ID_CODE]):
            connect_ui.user32.SendMessageW(eh, WM_SETTEXT, 0, ctypes.c_wchar_p(''))
        self.assertTrue(connect_ui.user32.PostMessageW(ip_eh, 0x0007, port_eh, 0))
        time.sleep(0.2)
        buf = ctypes.create_unicode_buffer(64)
        connect_ui.user32.SendMessageW(ip_eh, 0x000D, 64, buf)  # WM_GETTEXT
        self.assertEqual(buf.value, '', 'placeholder 不应是真实文本')
        connect_ui.user32.SendMessageW(ip_eh, WM_SETTEXT, 0, ctypes.c_wchar_p('10.0.0.9'))
        time.sleep(0.2)
        connect_ui.user32.SendMessageW(ip_eh, WM_SETTEXT, 0, ctypes.c_wchar_p(''))
        self.assertTrue(connect_ui.user32.PostMessageW(ip_eh, 0x0008, port_eh, 0))
        time.sleep(0.2)
        # 诊断：窗口是否还活着、是否属于本进程、PostMessage 是否成功
        alive = bool(connect_ui.user32.IsWindow(hwnd))
        pid = ctypes.c_ulong()
        tid = connect_ui.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        ok_post = connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self.assertTrue(closed.wait(5),
                        'WM_CLOSE 后 on_close 没被调用；alive=%s tid=%s pid=%s '
                        'post_ok=%s err=%s 日志=%r'
                        % (alive, tid, pid.value, ok_post,
                           ctypes.get_last_error(), logs))
        t.join(5)
        self.assertFalse(t.is_alive(), '向导线程没退出')
        self.assertFalse(done.is_set(), '没点连接却触发了 on_done')

    def test_only_code_uses_discovered_pairing_port(self):
        """只填 6 位配对码、IP 端口留空：电脑自己发现配对端口并填回输入框。"""
        closed = threading.Event()
        done = threading.Event()
        discovered = []

        def fake_discover(peers=(), timeout=None, browser=None):
            discovered.append(list(peers))
            return '192.168.137.144:43485'

        orig_discover = connect_ui.discover_pair_target
        connect_ui.discover_pair_target = fake_discover
        pair_cmds = []
        orig_runner = connect_ui.run_cmd

        def runner(cmd, timeout=25):
            j = ' '.join(cmd)
            pair_cmds.append(j)
            if ' pair ' in j:
                return 0, 'Successfully paired to 192.168.137.144:43485'
            if 'connect' in j:
                return 0, 'connected to 192.168.137.144:37999'
            return 0, 'List of devices attached\n'

        try:
            done_cb = done.set
            t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                                 kwargs=dict(adb='fake-adb', on_done=done_cb,
                                             on_close=closed.set, runner=runner))
            t.start()
            hwnd = _own_window(done_cb)
            self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
            ctx = connect_ui._WINDOWS[hwnd]
            edits = ctx['edits']
            WM_SETTEXT, WM_COMMAND = 0x000C, 0x0111
            connect_ui.user32.SendMessageW(edits[connect_ui.ID_CODE], WM_SETTEXT, 0,
                                           ctypes.c_wchar_p('123456'))
            connect_ui.user32.PostMessageW(hwnd, WM_COMMAND, connect_ui.ID_GO, 0)
            for _ in range(80):
                if any(' pair ' in c for c in pair_cmds):
                    break
                time.sleep(0.1)
            pair_cmd = next((c for c in pair_cmds if ' pair ' in c), None)
            self.assertIsNotNone(pair_cmd, '没发起 adb pair：%r' % pair_cmds)
            self.assertIn('192.168.137.144:43485', pair_cmd,
                          '没用自动发现的配对端口：%r' % pair_cmd)
            self.assertIn('123456', pair_cmd)
            self.assertTrue(discovered, '没有调用自动发现')
            # 发现的地址要填回输入框，让用户看到电脑找到了什么
            # （pair 之后还要等 10 秒的「连接端口」发现窗口，所以这里要等足够久）
            for _ in range(300):
                if connect_ui._text(edits[connect_ui.ID_IP]):
                    break
                time.sleep(0.1)
            self.assertEqual(connect_ui._text(edits[connect_ui.ID_IP]), '192.168.137.144')
            self.assertEqual(connect_ui._text(edits[connect_ui.ID_PORT]), '43485')
            connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
            self.assertTrue(closed.wait(5), 'WM_CLOSE 后 on_close 没被调用')
            t.join(5)
        finally:
            connect_ui.discover_pair_target = orig_discover
            connect_ui.run_cmd = orig_runner


if __name__ == '__main__':
    unittest.main()