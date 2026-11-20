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
from unittest import mock

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


def _click_at(hwnd, rect):
    """在客户区 rect 中心发一次真实的按下+抬起。"""
    bx, by, bw, bh = rect
    lp = (bx + bw // 2) | ((by + bh // 2) << 16)
    connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
    connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)


def _open_pair_panel(hwnd, timeout=5):
    """配对面板平时收着：点「手动配对」把它展开（展开后才有「连接手机」按钮）。"""
    ctx = connect_ui._WINDOWS[hwnd]
    if ctx.get('pair_open'):
        return
    _click_at(hwnd, ctx['hotspots'][connect_ui.ID_MANUAL])
    for _ in range(int(timeout / 0.1)):
        if ctx.get('pair_open'):
            return
        time.sleep(0.1)
    raise AssertionError('点了「手动配对」也没展开配对面板')


class _Isolated(unittest.TestCase):
    """测试不许碰真实局域网：手机真的在线时，向导的 _enrich_mdns 会用真 socket 组播一次，
    把真机混进假数据里（行数、名字都跟着变），那样测的就不是罐头数据了。
    只在本测试期间替掉 connect_ui 里的引用，不动 mdns_browse 模块本身。"""

    def setUp(self):
        self._orig_enrich = connect_ui._enrich_mdns
        connect_ui._enrich_mdns = lambda discovered, **kw: discovered

    def tearDown(self):
        connect_ui._enrich_mdns = self._orig_enrich


class WizardSmokeTest(_Isolated):
    def test_hover_only_on_connect_button_not_row(self):
        """悬停「连接」按钮 → 按钮变色、设备行不变色；悬停设备行其余位置 → 无任何 hover。"""
        closed = threading.Event()
        done = threading.Event()
        done_cb = done.set   # 绑定方法每次取属性都是新对象，先存引用再传+认窗口
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         on_close=closed.set, runner=fake_runner_mixed,
                                         known=[{'serial': '192.168.137.77:39001',
                                                 'name': '旧手机',
                                                 'addr': '192.168.137.77:39001'}]))
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
        self.assertFalse([r for r in rows if r['state'] == 'offline'],
                         '离线设备不该出现在列表里（历史里有，但它此刻既不能连也不能配）')
        base_btn, base_row = pixel(*btn_px), pixel(*row_px)
        self.assertEqual(base_btn, connect_ui.C_PRIMARY, '按钮常态应为主色')
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
            # 悬停按钮：与大按钮「连接手机」同款 hover——主色填深一档 + 白字，行保持白
            lp = btn_px[0] | (btn_px[1] << 16)
            u.PostMessageW(hwnd, 0x0200, 0, lp)
            time.sleep(0.4)
            self.assertEqual(ctx['hover'], connect_ui.ID_ROW_BASE + j, '按钮应响应悬停')
            self.assertEqual(pixel(*btn_px), connect_ui.C_PRIMARY_HOVER,
                             '悬停时按钮应像「连接手机」大按钮一样变深')
            self.assertNotEqual(connect_ui.C_PRIMARY_HOVER, connect_ui.C_PRIMARY,
                                'hover 色必须和常态肉眼可分辨')
            self.assertEqual(pixel(*row_px), connect_ui.C_SURFACE,
                             '悬停按钮时设备行不应变色（只有按钮 hover）')
        finally:
            connect_ui.user32.TrackMouseEvent = orig_tme
        u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self.assertTrue(closed.wait(5), '窗口没关闭')
        t.join(5)
        self.assertFalse(t.is_alive(), '向导线程没退出')

    def test_connected_row_not_clickable_and_found_row_asks_code(self):
        """已连接的设备行点了不能有任何反应；没连上的设备点「配对」只展开填码面板。

        用户 m02243：点设备项（还是已连接的设备）又触发了一次连接，开出第二个「裸」
        投屏窗口。所以行本身不再是热点，只有行尾「配对」按钮是。
        用户 m04638：没连上就一定要先配对——按钮只能是「配对」，点它不直接连。
        """
        done = threading.Event()
        closed = threading.Event()

        def done_cb(s, n=''):
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
        vis = ctx['visible_rows']
        online = [i for i, r in enumerate(vis) if r['state'] == 'online']
        self.assertTrue(online, '假数据里应该有已连接的设备')
        # 已连接（在线）的设备不显示「连接」按钮，整行也不再是热点
        for i in online:
            self.assertNotIn(connect_ui.ID_ROW_BASE + i, ctx['hotspots'],
                             '在线设备不应有连接按钮')
        found = [j for j, r in enumerate(vis) if r['state'] == 'found']
        self.assertTrue(found, '假数据里应该有一台没连上的设备')
        for j in found:
            self.assertIn(connect_ui.ID_ROW_BASE + j, ctx['hotspots'],
                          '没连上的设备要保留「配对」按钮')
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
        # 2) 点没连上的设备右侧「配对」按钮：只展开填码面板，不发起任何连接
        j = found[0]
        bx, by, bw, bh = ctx['hotspots'][connect_ui.ID_ROW_BASE + j]
        lp = (bx + bw // 2) | ((by + bh // 2) << 16)
        connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
        connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)
        for _ in range(40):
            if ctx['pair_open']:
                break
            time.sleep(0.1)
        self.assertTrue(ctx['pair_open'], '点「配对」要把配对面板展开')
        self.assertEqual(ctx['pair_mode'], 'prefill', 'IP 已知 → 只让填配对码')
        self.assertEqual(ctx.get('toast'), '', '没填配对码之前不该显示「连接成功」')
        self.assertFalse(done.is_set(), '没填配对码之前不该回调 on_done')
        connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        self.assertTrue(closed.wait(5))
        t.join(5)
        self.assertFalse(t.is_alive(), '向导线程没退出')

    def test_unpaired_found_row_asks_for_pairing_code(self):
        """没跟本机配对过的「新发现」设备：点按钮不发起连接，只等配对码。

        用户 m03416：新发现 = 广播可见但没连上，可能从没配对过，点「连接」注定失败，
        所以这种行按钮叫「配对」，点了展开配对面板、焦点交给配对码框。
        用户 m04211：列表里那个 ip:端口 是**投屏用的连接端口**，配对端口是另一个
        随机端口（手机「使用配对码配对设备」页面上那个），所以绝不能预填进端口框——
        预填了就会拿连接端口去配对，必然失败。
        """
        done = threading.Event()
        closed = threading.Event()
        cmds = []
        discovered = []
        result = {}

        def done_cb(s, n='', a=''):
            result.update(serial=s, name=n)
            done.set()

        def fake_discover(peers=(), timeout=None, browser=None, name='', log=None):
            discovered.append(list(peers))
            return '192.168.137.88:43485'

        def runner(cmd, timeout=25):
            joined = ' '.join(cmd)
            cmds.append(joined)
            if 'devices' in joined:
                return 0, 'List of devices attached\n'   # 一台都没连上
            if 'mdns' in joined:
                return 0, ('List of discovered mdns services\n'
                           'adb-aaaa1111-NewPhone\t_adb-tls-connect._tcp\t'
                           '192.168.137.88:41234\n')
            if ' pair ' in joined:
                return 0, 'Successfully paired to 192.168.137.88:43485'
            if 'connect' in joined:
                return 0, 'connected to 192.168.137.88:43485'
            return 0, ''

        orig_discover = connect_ui.discover_pair_target
        connect_ui.discover_pair_target = fake_discover
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         on_close=closed.set, runner=runner))
        try:
            t.start()
            hwnd = _own_window(done_cb)
            self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
            ctx = connect_ui._WINDOWS[hwnd]
            for _ in range(80):
                if ctx['visible_rows']:
                    break
                time.sleep(0.1)
            row = ctx['visible_rows'][0]
            self.assertEqual(row['state'], 'found')
            self.assertNotIn('台设备', ctx['status_text'],
                             '刷完不用再报「找到 N 台设备」，列表本身就是答案')
            bx, by, bw, bh = ctx['hotspots'][connect_ui.ID_ROW_BASE]
            lp = (bx + bw // 2) | ((by + bh // 2) << 16)
            connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
            connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)
            for _ in range(40):
                if connect_ui._text(ctx['edits'][connect_ui.ID_IP]):
                    break
                time.sleep(0.1)
            self.assertTrue(ctx['pair_open'], '点「配对」应把配对面板展开')
            self.assertEqual(ctx['pair_mode'], 'prefill', 'IP 已知 → prefill 模式')
            self.assertEqual(connect_ui._text(ctx['edits'][connect_ui.ID_IP]),
                             '192.168.137.88', '该把这一行的 IP 自动填好')
            self.assertEqual(connect_ui._text(ctx['edits'][connect_ui.ID_PORT]), '',
                             '连接端口不是配对端口，绝不能预填进端口框')
            self.assertEqual(ctx['pair_label'], '192.168.137.88',
                             '框上方只报 IP（配对端口要到配对时才问设备）')
            self.assertFalse(connect_ui.user32.IsWindowVisible(
                ctx['edits'][connect_ui.ID_IP]),
                'prefill 模式下 IP 框应藏起来（已经填好，用户不用看）')
            self.assertTrue(connect_ui.user32.IsWindowVisible(
                ctx['edits'][connect_ui.ID_CODE]), '配对码框要可见')
            self.assertIn('配对码', ctx['status_text'], '状态行要提示去填配对码')
            self.assertFalse(any(' pair ' in c or ('connect' in c and 'mdns' not in c)
                                 for c in cmds),
                             '没配对过就不该直接发起连接：%r' % cmds)
            # 填好 6 位码点「连接手机」：配对端口必须来自设备的配对广播，
            # 而不是列表里那个连接端口（用户 m04211 的失败原因）
            connect_ui.user32.SendMessageW(ctx['edits'][connect_ui.ID_CODE],
                                           0x000C, 0, ctypes.c_wchar_p('123456'))
            connect_ui.user32.PostMessageW(hwnd, 0x0111, connect_ui.ID_GO, 0)
            pair_cmd = None
            for _ in range(80):
                pair_cmd = next((c for c in cmds if ' pair ' in c), None)
                if pair_cmd:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(pair_cmd, '没发起 adb pair：%r' % cmds)
            self.assertIn('192.168.137.88:43485', pair_cmd,
                          '必须用设备广播的配对端口：%r' % pair_cmd)
            self.assertNotIn('41234', pair_cmd,
                             '不能用列表里的连接端口去配对：%r' % pair_cmd)
            self.assertIn('123456', pair_cmd)
            self.assertTrue(discovered, '没有走自动发现')
            # 配对成功后：浅绿「连接成功」toast → 1 秒后窗口自动消失、回调 on_done
            saw_toast = False
            for _ in range(400):
                if ctx.get('toast'):
                    saw_toast = True
                    break
                if done.is_set():
                    break
                time.sleep(0.05)
            self.assertTrue(saw_toast, '配对成功后没有显示「连接成功」提示')
            self.assertEqual(ctx['toast'], '连接成功')
            self.assertTrue(done.wait(6), 'toast 后窗口没有自动关闭')
            self.assertEqual(result.get('serial'), '192.168.137.88:41234',
                             'on_done 应带回 adb 报的串号')
            for _ in range(60):
                if not connect_ui.user32.IsWindow(hwnd):
                    break
                time.sleep(0.05)
            self.assertFalse(connect_ui.user32.IsWindow(hwnd), '窗口应已自动销毁')
        finally:
            connect_ui.discover_pair_target = orig_discover
            connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)
            closed.wait(5)
            t.join(5)
            self.assertFalse(t.is_alive())

    def test_offline_rows_are_not_listed(self):
        """离线设备不进列表：此刻既连不上也配不了，只会是个死按钮。

        用户 m04505：离线说明手机根本没开无线调试，本来就不能配对/连接；
        而它一开无线调试就会以「新发现」出现——所以离线行没有存在意义。
        """
        done = threading.Event()
        closed = threading.Event()

        def runner(cmd, timeout=25):
            joined = ' '.join(cmd)
            if 'devices' in joined:
                return 0, 'List of devices attached\n'   # 一台都没连上
            if 'mdns' in joined:
                return 0, 'List of discovered mdns services\n'   # 也没人广播
            return 0, ''

        known = [{'serial': 'adb-bfd7e543-MoDo9u._adb-tls-connect._tcp',
                  'name': '一加 13', 'addr': '192.168.137.77:39001'},
                 {'serial': 'adb-cccc3333-Lost._adb-tls-connect._tcp',
                  'name': 'adb-cccc3333-Lost._adb-tls-connect._tcp', 'addr': ''}]
        done_cb = done.set
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         on_close=closed.set, runner=runner,
                                         known=known))
        t.start()
        hwnd = _own_window(done_cb)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        ctx = connect_ui._WINDOWS[hwnd]
        for _ in range(80):
            if ctx['rows']:
                break
            time.sleep(0.1)
        self.assertTrue([r for r in ctx['rows'] if r['state'] == 'offline'],
                        '数据层仍会带出历史里的离线设备（供去重/名字用）')
        self.assertEqual(ctx['visible_rows'], [],
                         '但列表里一台都不该显示：%r' % (ctx['visible_rows'],))
        self.assertEqual(ctx['hotspots'].get(connect_ui.ID_ROW_BASE), None,
                         '没行就不该有行内按钮热点')
        self.assertFalse(ctx['pair_open'], '一台能操作的设备都没有也不该自己摊开配对面板')
        connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        self.assertTrue(closed.wait(5))
        t.join(5)
        self.assertFalse(t.is_alive())

    def test_auto_refresh_timer_started(self):
        """向导开着时每 3 秒自动刷新列表（手机重开无线调试后自己变回「新发现」）。"""
        timers = []
        orig = connect_ui.user32.SetTimer

        def spy(hwnd, tid, ms, cb):
            timers.append((tid, ms))
            return orig(hwnd, tid, ms, cb)

        connect_ui.user32.SetTimer = spy
        closed = threading.Event()
        done_cb = closed.set
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         on_close=closed.set,
                                         runner=fake_runner_mixed))
        try:
            t.start()
            hwnd = _own_window(done_cb)
            self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
            time.sleep(0.5)
            connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)
            closed.wait(5)
            t.join(5)
        finally:
            connect_ui.user32.SetTimer = orig
        self.assertIn((connect_ui.AUTO_REFRESH_TIMER, connect_ui.AUTO_REFRESH_MS),
                      timers, '没设置自动刷新定时器：%r' % timers)

    def test_pair_panel_hidden_by_default_and_manual_link_opens_it(self):
        """配对面板平时收着：列表更高、页面更清爽；点「手动配对」才展开三个框。"""
        closed = threading.Event()
        done_cb = closed.set
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         on_close=closed.set,
                                         runner=fake_runner_mixed))
        t.start()
        hwnd = _own_window(done_cb)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        ctx = connect_ui._WINDOWS[hwnd]
        for _ in range(80):
            if ctx['rows']:
                break
            time.sleep(0.1)
        self.assertFalse(ctx['pair_open'], '默认应收起配对面板')
        self.assertIn(connect_ui.ID_MANUAL, ctx['hotspots'], '收起时应显示「手动配对」链接')
        self.assertNotIn(connect_ui.ID_GO, ctx['hotspots'], '收起时不该有「连接手机」大按钮')
        for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
            self.assertFalse(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]),
                             '收起时输入框都要藏起来')
        self.assertGreater(connect_ui._list_h(ctx), connect_ui.LIST_H,
                           '收起时列表要变高，多显示几台设备')
        bx, by, bw, bh = ctx['hotspots'][connect_ui.ID_MANUAL]
        lp = (bx + bw // 2) | ((by + bh // 2) << 16)
        connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
        connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)
        time.sleep(0.4)
        self.assertTrue(ctx['pair_open'], '点「手动配对」应展开面板')
        self.assertEqual(ctx['pair_mode'], 'manual')
        # 面板展开后窗口回到全高，收起时矮一截（底部别留大片空白）
        rc = wt.RECT()
        connect_ui.user32.GetClientRect(hwnd, ctypes.byref(rc))
        self.assertEqual(rc.bottom, connect_ui.WIN_H, '展开面板时窗口该是全高')
        self.assertLess(connect_ui.WIN_H_CLOSED, connect_ui.WIN_H)
        for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
            self.assertTrue(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]),
                            '手动模式下三个框都要可见')
        self.assertIn(connect_ui.ID_GO, ctx['hotspots'], '展开后才有「连接手机」大按钮')
        # 手动填的旧地址不能留到下次：收起再展开应清空 IP/端口
        u = connect_ui.user32
        u.SendMessageW(ctx['edits'][connect_ui.ID_IP], 0x000C, 0,
                       ctypes.c_wchar_p('10.0.0.9'))
        u.SendMessageW(ctx['edits'][connect_ui.ID_PORT], 0x000C, 0,
                       ctypes.c_wchar_p('5555'))
        time.sleep(0.2)
        # 收起
        cx, cy, cw, ch = ctx['hotspots'][connect_ui.ID_PAIR_CLOSE]
        lp = (cx + cw // 2) | ((cy + ch // 2) << 16)
        connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
        connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)
        time.sleep(0.4)
        self.assertFalse(ctx['pair_open'], '点「收起」应再收起来')
        self.assertNotIn(connect_ui.ID_GO, ctx['hotspots'])
        connect_ui.user32.GetClientRect(hwnd, ctypes.byref(rc))
        self.assertEqual(rc.bottom, connect_ui.WIN_H_CLOSED,
                         '收起面板后窗口要变矮（底部没有内容了）')
        # 再次展开：上一次手填的 IP/端口 必须被清掉（旧地址只会让人填错）
        bx, by, bw, bh = ctx['hotspots'][connect_ui.ID_MANUAL]
        lp = (bx + bw // 2) | ((by + bh // 2) << 16)
        connect_ui.user32.PostMessageW(hwnd, 0x0201, 1, lp)
        connect_ui.user32.PostMessageW(hwnd, 0x0202, 0, lp)
        time.sleep(0.4)
        self.assertTrue(ctx['pair_open'])
        self.assertEqual(connect_ui._text(ctx['edits'][connect_ui.ID_IP]), '',
                         '手动配对时要清空 IP 框')
        self.assertEqual(connect_ui._text(ctx['edits'][connect_ui.ID_PORT]), '',
                         '手动配对时要清空端口框')
        # 用户 m04090：展开后底部提示不能被 3 秒自动刷新悄悄清掉
        self.assertIn('配对码', ctx['status_text'])
        time.sleep(4.0)          # 跨过至少一次自动刷新
        self.assertIn('配对码', ctx['status_text'],
                      '自动刷新不该把「手动配对」的提示清掉')
        connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        self.assertTrue(closed.wait(5))
        t.join(5)
        self.assertFalse(t.is_alive())

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
        _open_pair_panel(hwnd)                     # 配对面板平时收着，先点「手动配对」展开
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

        def fake_discover(peers=(), timeout=None, browser=None, name='', log=None):
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
            _open_pair_panel(hwnd)   # 面板收着时没有「连接手机」按钮，先展开
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
            # 配对端口用过就失效，不能回填到输入框（否则下次会被当成连接端口，必然 10061）
            for _ in range(300):
                if not ctx['busy']:
                    break
                time.sleep(0.1)
            self.assertEqual(connect_ui._text(edits[connect_ui.ID_IP]), '',
                             '配对端口不该留在 IP 框里')
            self.assertEqual(connect_ui._text(edits[connect_ui.ID_PORT]), '',
                             '配对端口不该留在端口框里')
            self.assertIn('192.168.137.144:43485', ctx['busy_text'],
                          '忙碌状态里应告诉用户电脑找到了什么：%r' % ctx['busy_text'])
            connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
            self.assertTrue(closed.wait(5), 'WM_CLOSE 后 on_close 没被调用')
            t.join(5)
        finally:
            connect_ui.discover_pair_target = orig_discover
            connect_ui.run_cmd = orig_runner


MANY_MDNS = 'List of discovered mdns services\n' + ''.join(
    'adb-phone%d\t_adb-tls-connect._tcp.\t192.168.137.%d:41234\n' % (i, 30 + i)
    for i in range(8))


def fake_runner_many(cmd, timeout=25):
    """8 台「新发现」设备：超过收起状态能显示的 6 行，逼出滚动。"""
    joined = ' '.join(cmd)
    if 'devices' in joined:
        return 0, 'List of devices attached\n\n'
    if 'mdns' in joined:
        return 0, MANY_MDNS
    if 'getprop' in joined:
        return 0, 'PJZ110\n'
    return 0, ''


def fake_runner_none(cmd, timeout=25):
    """一台设备都没有（手机没开无线调试 / mDNS 被挡）。"""
    joined = ' '.join(cmd)
    if 'devices' in joined:
        return 0, 'List of devices attached\n\n'
    if 'mdns' in joined:
        return 0, 'List of discovered mdns services\n'
    return 0, ''


def _wheel(hwnd, client_x, client_y, notches):
    """在客户区某点滚一格滚轮（notches>0 往上，<0 往下）。"""
    u = connect_ui.user32
    pt = wt.POINT(client_x, client_y)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    wparam = ((notches * 120) & 0xFFFF) << 16
    u.PostMessageW(hwnd, connect_ui.WM_MOUSEWHEEL, wparam, lparam)


def _wait_refresh(ctx, timeout=10):
    """等第一次刷新落盘：忙起来又落下就说明结果到了（空列表也一样）。"""
    saw_busy = False
    for _ in range(int(timeout / 0.05)):
        if ctx['busy']:
            saw_busy = True
        elif saw_busy:
            return
        time.sleep(0.05)


class ListScrollTest(_Isolated):
    """列表卡片固定高度；设备多了滚轮翻；一台都没有时自动摊开配对面板。"""

    def _open(self, runner, done_cb):
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         runner=runner))
        t.start()
        hwnd = _own_window(done_cb)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        return t, hwnd, connect_ui._WINDOWS[hwnd]

    def _close(self, hwnd, t):
        connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        t.join(5)

    def test_card_height_is_fixed_and_extra_rows_scroll(self):
        done = threading.Event()
        t, hwnd, ctx = self._open(fake_runner_many, done.set)
        try:
            _wait_refresh(ctx)
            self.assertGreater(len(ctx['rows']), connect_ui.MAX_ROWS_FULL,
                               '构造的 8 台设备没进来：%r' % ctx['rows'])
            # 收起时固定 6 行高，不随行数长高
            self.assertEqual(connect_ui._list_h(ctx),
                             connect_ui.ROW_H * connect_ui.MAX_ROWS_FULL)
            self.assertEqual(len(ctx['visible_rows']), connect_ui.MAX_ROWS_FULL)
            first = ctx['visible_rows'][0]['serial']
            # 往下滚一格：看到第 2 台起的 6 行，卡片高度不变
            _wheel(hwnd, connect_ui.PAD + 200, connect_ui.LIST_Y + 40, -1)
            for _ in range(30):
                if ctx['scroll']:
                    break
                time.sleep(0.1)
            self.assertEqual(ctx['scroll'], 1, '滚轮没把列表滚动起来')
            self.assertEqual(ctx['visible_rows'][0]['serial'],
                             ctx['rows'][1]['serial'])
            self.assertNotEqual(ctx['visible_rows'][0]['serial'], first)
            self.assertEqual(connect_ui._list_h(ctx),
                             connect_ui.ROW_H * connect_ui.MAX_ROWS_FULL)
            # 往上滚回顶部
            _wheel(hwnd, connect_ui.PAD + 200, connect_ui.LIST_Y + 40, 1)
            for _ in range(30):
                if not ctx['scroll']:
                    break
                time.sleep(0.1)
            self.assertEqual(ctx['scroll'], 0)
            self.assertEqual(ctx['visible_rows'][0]['serial'], first)
        finally:
            self._close(hwnd, t)

    def test_usb_tab_hides_pairing_inputs_and_keeps_panel_closed(self):
        done = threading.Event()
        t, hwnd, ctx = self._open(fake_runner_none, done.set)
        try:
            _wait_refresh(ctx)
            self.assertFalse(ctx['pair_open'], '列表是空的也不该自己摊开配对面板')
            _click_at(hwnd, ctx['hotspots'][connect_ui.ID_MANUAL])
            for _ in range(100):
                if ctx.get('pair_open'):
                    break
                time.sleep(0.1)
            self.assertTrue(ctx['pair_open'], '点「手动配对」没打开面板')

            _click_at(hwnd, ctx['hotspots'][connect_ui.ID_TAB_USB])
            for _ in range(50):
                if ctx['tab'] == 'usb':
                    break
                time.sleep(0.1)
            self.assertEqual(ctx['tab'], 'usb')
            # 配对面板只属于无线页：三个输入框是原生子窗口，不藏起来就会浮在
            # USB 提示文字上面（用户就是这么看到的）
            self.assertFalse(ctx['pair_open'], 'USB 页还挂着配对面板')
            for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
                self.assertFalse(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]),
                                 'USB 页露出了配对输入框 %d' % eid)
            self.assertNotIn(connect_ui.ID_GO, ctx['hotspots'])
            self.assertEqual(connect_ui._win_h(ctx), connect_ui.WIN_H_CLOSED)

            # 切回无线页：用户没点「手动配对」，面板不该自己弹出来
            _click_at(hwnd, ctx['hotspots'][connect_ui.ID_TAB_WIFI])
            for _ in range(50):
                if ctx['tab'] == 'wifi':
                    break
                time.sleep(0.1)
            self.assertEqual(ctx['tab'], 'wifi')
            time.sleep(1.2)          # 跨过一次自动刷新，别让它偷偷把面板摊开
            self.assertFalse(ctx['pair_open'], '切回无线页时配对面板自己弹出来了')
            for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
                self.assertFalse(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]))
            self.assertIn(connect_ui.ID_MANUAL, ctx['hotspots'])
        finally:
            self._close(hwnd, t)

    def test_pair_panel_refuses_to_open_on_usb_tab(self):
        """自动刷新/迟到的发现撞上 USB 页时，面板不能摊开（否则输入框糊在 USB 页上）。"""
        done = threading.Event()
        t, hwnd, ctx = self._open(fake_runner_none, done.set)
        try:
            _wait_refresh(ctx)
            _click_at(hwnd, ctx['hotspots'][connect_ui.ID_TAB_USB])
            for _ in range(50):
                if ctx['tab'] == 'usb':
                    break
                time.sleep(0.1)
            connect_ui._set_pair_open(ctx, 'manual')
            self.assertFalse(ctx['pair_open'], 'USB 页被摊开了配对面板')
            for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
                self.assertFalse(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]),
                                 'USB 页露出了配对输入框 %d' % eid)
        finally:
            self._close(hwnd, t)

    def test_empty_list_keeps_panel_closed(self):
        """打开窗口时一台设备都没有：只看到列表空态和「手动配对」链接，别塞表单过来。"""
        done = threading.Event()
        t, hwnd, ctx = self._open(fake_runner_none, done.set)
        try:
            _wait_refresh(ctx)
            self.assertFalse(ctx['pair_open'], '空列表把配对面板自己摊开了')
            self.assertNotIn(connect_ui.ID_GO, ctx['hotspots'])
            self.assertIn(connect_ui.ID_MANUAL, ctx['hotspots'])
            for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
                self.assertFalse(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]))
            self.assertEqual(connect_ui._win_h(ctx), connect_ui.WIN_H_CLOSED)
        finally:
            self._close(hwnd, t)


class QrPanelTest(_Isolated):
    """面板里能切到「二维码」：切过去窗口变高、三个输入框全藏、没有大按钮。"""

    def _open(self, runner, done_cb):
        t = threading.Thread(target=connect_ui.show_wizard, daemon=True,
                             kwargs=dict(adb='fake-adb', on_done=done_cb,
                                         runner=runner))
        t.start()
        hwnd = _own_window(done_cb)
        self.assertTrue(hwnd, '向导窗口 10 秒内没出现')
        return t, hwnd, connect_ui._WINDOWS[hwnd]

    def test_switch_to_qr_hides_inputs_and_grows_window(self):
        stop = threading.Event()

        def fake_qr(adb, service, password, runner=None, log=None, wait=None,
                    browser=None, cancel=None, peers=()):
            # 假装在等设备扫码：一直挂到测试结束时放行
            stop.wait(15)
            return False, '已取消扫码配对。', ''

        def done_cb(serial, name='', addr=''):
            pass

        t, hwnd, ctx = self._open(fake_runner, done_cb)
        try:
            _wait_refresh(ctx)
            _open_pair_panel(hwnd)
            with mock.patch.object(connect_ui, 'qr_pair', fake_qr):
                # 切到二维码
                _click_at(hwnd, ctx['hotspots'][connect_ui.ID_MODE_QR])
                for _ in range(50):
                    if ctx.get('pair_qr'):
                        break
                    time.sleep(0.1)
                self.assertTrue(ctx.get('pair_qr'), '没切到二维码模式')
                self.assertTrue(ctx['qr_rows'], '二维码矩阵是空的')
                for eid in (connect_ui.ID_IP, connect_ui.ID_PORT, connect_ui.ID_CODE):
                    self.assertFalse(connect_ui.user32.IsWindowVisible(ctx['edits'][eid]),
                                     '二维码模式不该露出输入框 %d' % eid)
                self.assertNotIn(connect_ui.ID_GO, ctx['hotspots'],
                                 '二维码模式没有「连接设备」按钮')
                rc = connect_ui.wt.RECT()
                connect_ui.user32.GetClientRect(hwnd, ctypes.byref(rc))
                self.assertEqual(rc.bottom, connect_ui.WIN_H_QR,
                                 '二维码模式窗口要高一些才摆得下码')
                # 变高之后新露出来的那一条也得画到（曾经留着一条黑边没人画）
                u, g = connect_ui.user32, connect_ui.gdi32
                u.GetDC.argtypes = [connect_ui.wt.HWND]
                u.GetDC.restype = connect_ui.wt.HDC
                u.ReleaseDC.argtypes = [connect_ui.wt.HWND, connect_ui.wt.HDC]
                g.GetPixel.argtypes = [connect_ui.wt.HDC, ctypes.c_int, ctypes.c_int]
                hdc = u.GetDC(hwnd)
                try:
                    for yy in (rc.bottom - 12, rc.bottom - 6, rc.bottom - 1):
                        self.assertNotEqual(g.GetPixel(hdc, 260, yy), 0,
                                            '底部 y=%d 是没画到的黑边' % yy)
                finally:
                    u.ReleaseDC(hwnd, hdc)
                # 再点一次「二维码」不再是换码（换码交给面板里的「刷新」）
                old_creds = ctx['qr_creds']
                _click_at(hwnd, ctx['hotspots'][connect_ui.ID_MODE_QR])
                time.sleep(0.4)
                self.assertEqual(ctx['qr_creds'], old_creds, '同一模式下不该自己换码')
                # 点「刷新」才换一张新码
                self.assertIn(connect_ui.ID_QR_REFRESH, ctx['hotspots'], '二维码面板没有「刷新」')
                _click_at(hwnd, ctx['hotspots'][connect_ui.ID_QR_REFRESH])
                for _ in range(50):
                    if ctx['qr_creds'] != old_creds:
                        break
                    time.sleep(0.1)
                self.assertNotEqual(ctx['qr_creds'], old_creds, '点「刷新」没换新码')
                self.assertTrue(ctx.get('pair_qr'))
                # 切回配对码：窗口变回原高、输入框回来、大按钮回来
                _click_at(hwnd, ctx['hotspots'][connect_ui.ID_MODE_CODE])
                for _ in range(50):
                    if not ctx.get('pair_qr'):
                        break
                    time.sleep(0.1)
                self.assertFalse(ctx.get('pair_qr'), '切不回配对码模式')
                connect_ui.user32.GetClientRect(hwnd, ctypes.byref(rc))
                self.assertEqual(rc.bottom, connect_ui.WIN_H)
                self.assertTrue(connect_ui.user32.IsWindowVisible(
                    ctx['edits'][connect_ui.ID_CODE]))
                self.assertIn(connect_ui.ID_GO, ctx['hotspots'])
                self.assertNotIn('等待设备扫码', ctx['status_text'],
                                 '切回配对码后状态行还停在扫码提示上')
                self.assertEqual(ctx['status_text'], connect_ui.PAIR_HINT_MANUAL,
                                 '来回切一次，配对码模式的提示语不该变')
        finally:
            stop.set()
            self._close(hwnd, t)

    def test_qr_wait_does_not_cancel_itself(self):
        """扫码任务刚起身不能认为「已被取消」。

        曾经的 bug：收起面板（开窗时、或切到 USB 页）会把取消标志置真，而进入二维码模式
        时没复位，于是每一次扫码任务第一轮就自我退出——一次广播都不问，界面永远停在
        「等待设备扫码」，手机一直「正在配对设备」。
        """
        seen = {}

        def fake_qr(adb, service, password, runner=None, log=None, wait=None,
                    browser=None, cancel=None, peers=()):
            seen['service'] = service
            seen['cancelled_at_start'] = bool(cancel and cancel())
            return False, '没扫到码。', ''

        def done_cb(serial, name='', addr=''):
            pass

        t, hwnd, ctx = self._open(fake_runner, done_cb)
        try:
            _wait_refresh(ctx)
            _open_pair_panel(hwnd)
            with mock.patch.object(connect_ui, 'qr_pair', fake_qr):
                _click_at(hwnd, ctx['hotspots'][connect_ui.ID_MODE_QR])
                for _ in range(80):
                    if seen:
                        break
                    time.sleep(0.1)
                self.assertTrue(seen, '点了「二维码」却没去等扫码')
                self.assertFalse(seen['cancelled_at_start'],
                                 '扫码任务一上来就以为「已取消」了，等于永远等不到设备')
                self.assertIsNotNone(ctx.get('qr_token'), '等码的令牌没登记')
                token = ctx['qr_token']
                seen.clear()
                _click_at(hwnd, ctx['hotspots'][connect_ui.ID_QR_REFRESH])
                for _ in range(80):
                    if seen:
                        break
                    time.sleep(0.1)
                self.assertTrue(seen, '点「刷新」后没重新等码')
                self.assertFalse(seen['cancelled_at_start'], '换码后的新任务也被当成「已取消」')
                self.assertIsNot(ctx['qr_token'], token, '换码后令牌应该换新的')
        finally:
            self._close(hwnd, t)

    def _close(self, hwnd, t):
        connect_ui.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        t.join(5)


if __name__ == '__main__':
    unittest.main()