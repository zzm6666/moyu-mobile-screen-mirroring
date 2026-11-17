# -*- coding: utf-8 -*-
"""回归测试：watchdog 的「投屏窗口 60 秒仍未出现」误报。

复现场景（2026-11-17 实际故障日志）：
  10:16:04 程序启动（state['started_at'] 在模块导入时定格）
  10:16:12 自动找设备失败 → 弹连接向导
  10:23:50 用户在向导里配对成功（距启动约 466 秒）→ scrcpy launched
  10:23:52 scrcpy 窗口出现（child ready），投屏正常
  但在 Popen 之后、窗口出现之前的 1~2 秒里，watchdog 用「距程序启动的时间」
  判断超时 → 466s > 60s → 弹出错误弹窗，而投屏其实是好的。

正确语义：60 秒应该从「本次 scrcpy 进程启动」起算，而不是程序启动。
运行：.venv\\Scripts\\python.exe -m unittest discover -s tools -p "test_*.py"
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror_app as app


class FakeProc:
    """模拟 Popen 出来的 scrcpy：一直活着（poll() 返回 None）。"""
    pid = 4242

    def poll(self):
        return None


class WatchdogTimeoutTest(unittest.TestCase):
    def setUp(self):
        # 快照并隔离全局状态，避免测试互相污染
        self._saved = dict(app.state)
        self._saved_cfg = app.CFG
        self._fail_calls = []
        self._orig = {
            'fail': app.fail,
            'target_hwnd': app.target_hwnd,
            'list_devices': app.list_devices,
            'quit_app': app.quit_app,
            'try_auto_connect': app.connect_ui.try_auto_connect,
            'get_phone_aspect': app.get_phone_aspect,
            'has_audio_device': app.has_audio_device,
            'Popen': app.subprocess.Popen,
        }
        app.fail = lambda msg: self._fail_calls.append(msg)
        app.target_hwnd = lambda: None      # scrcpy 窗口尚未出现
        app.list_devices = lambda: ['192.168.1.2:5555']
        app.quit_app = lambda: None
        app.state.update({
            'frame': None, 'child': None, 'hidden': False,
            'proc': FakeProc(), 'quitting': False, 'error_shown': False,
            'retries': 0, 'serial': '192.168.1.2:5555',
        })

    def tearDown(self):
        for k, v in self._orig.items():
            if k == 'try_auto_connect':
                app.connect_ui.try_auto_connect = v
            elif k == 'Popen':
                app.subprocess.Popen = v
            else:
                setattr(app, k, v)
        app.state.clear()
        app.state.update(self._saved)
        app.CFG = self._saved_cfg

    def test_wizard_delay_must_not_trigger_false_timeout(self):
        """程序启动 466 秒后才配对成功、scrcpy 刚拉起 2 秒：不应报 60 秒超时。"""
        app.connect_ui.try_auto_connect = lambda adb, serial, runner=None: '192.168.1.2:5555'
        app.get_phone_aspect = lambda: 0.4545
        app.has_audio_device = lambda: True
        app.subprocess.Popen = lambda *a, **kw: FakeProc()
        app.state['started_at'] = time.time() - 466  # 用户在向导里停留了很久
        app.start_scrcpy()                           # 配对成功 → 拉起 scrcpy
        app.state['started_at'] -= 2                 # 窗口还没出现，已等 2 秒
        app.watchdog(None)
        self.assertEqual(
            self._fail_calls, [],
            '配对耗时被算进了投屏窗口等待时间，弹出误报：%r' % self._fail_calls)

    def test_real_timeout_still_reported(self):
        """scrcpy 拉起后真的超过 60 秒没窗口：仍要报错（原有保护不能丢）。"""
        app.state['started_at'] = time.time() - 61
        app.watchdog(None)
        self.assertEqual(len(self._fail_calls), 1)
        self.assertIn('60 秒', self._fail_calls[0])

    def test_start_scrcpy_resets_timer(self):
        """每次拉起 scrcpy（含向导成功、掉线重试路径）都应重置等待计时。"""
        app.state['started_at'] = time.time() - 466
        app.connect_ui.try_auto_connect = lambda adb, serial, runner=None: '192.168.1.2:5555'
        app.get_phone_aspect = lambda: 0.4545
        app.has_audio_device = lambda: True
        app.subprocess.Popen = lambda *a, **kw: FakeProc()
        before = time.time()
        app.start_scrcpy()
        self.assertGreaterEqual(app.state['started_at'], before,
                                'start_scrcpy 没有重置 started_at，等待计时仍从程序启动算起')
        self.assertLessEqual(app.state['started_at'], time.time())

    def test_connected_serial_skips_reconnect(self):
        """向导里点「已连接」设备：串号已在线就不该再 connect，直接拉 scrcpy。"""
        serial = '192.168.137.72:37881'
        app.CFG = dict(app.DEFAULT_CFG)
        app.CFG['serial'] = serial
        app.state['serial'] = serial
        app.list_devices = lambda: [serial]
        calls = []
        orig_run = app.subprocess.run
        app.subprocess.run = lambda cmd, **kw: calls.append(cmd) or orig_run(cmd, **kw)
        app.connect_ui.try_auto_connect = lambda *a, **kw: self.fail(
            '已连接的设备不应再走 try_auto_connect 重新找设备/连接')
        app.get_phone_aspect = lambda: 0.4545
        app.has_audio_device = lambda: True
        app.subprocess.Popen = lambda *a, **kw: FakeProc()
        try:
            app.start_scrcpy()
        finally:
            app.subprocess.run = orig_run
        self.assertFalse([c for c in calls if 'connect' in c],
                         '设备已在线却又执行了 adb connect：%r' % calls)
        self.assertIsInstance(app.state['proc'], FakeProc)


class WizardWiringTest(unittest.TestCase):
    """连接向导接线：known_devices 记忆 + on_done(serial, name) 新签名。"""

    def setUp(self):
        self._saved_state = dict(app.state)
        self._saved_cfg = app.CFG
        self._orig_show = app.connect_ui.show_wizard
        self._orig_save = app.save_cfg
        self._orig_start = app.start_scrcpy
        self._save_calls = []
        self._wiz_kwargs = {}
        app.CFG = dict(app.DEFAULT_CFG)
        app.CFG['known_devices'] = []
        app.state.update({'wizard_open': False, 'retries': 2, 'error_shown': True})
        app.start_scrcpy = lambda: None   # 不真拉 scrcpy

        def fake_show(adb, on_done, on_log=None, on_close=None, known=None, runner=None,
                      icon_path=None):
            self._wiz_kwargs = {'on_done': on_done, 'on_close': on_close, 'known': known,
                                'icon_path': icon_path}

        app.connect_ui.show_wizard = fake_show
        app.save_cfg = lambda path, cfg: self._save_calls.append((path, dict(cfg))) or True

    def tearDown(self):
        app.connect_ui.show_wizard = self._orig_show
        app.save_cfg = self._orig_save
        app.start_scrcpy = self._orig_start
        app.CFG = self._saved_cfg
        app.state.clear()
        app.state.update(self._saved_state)

    def test_wizard_gets_known_devices(self):
        """向导应拿到历史设备列表，才能显示「离线」行供一键重连。"""
        app.CFG['known_devices'] = [{'serial': '192.168.1.5:41001', 'name': '一加 13',
                                     'addr': '192.168.1.5:41001', 'last_seen': 1}]
        app.open_connect_wizard()
        self.assertEqual(self._wiz_kwargs['known'], app.CFG['known_devices'])

    def test_wizard_gets_app_icon(self):
        """向导窗口图标必须用魔芋应用图标 app.ico。"""
        app.open_connect_wizard()
        self.assertEqual(self._wiz_kwargs['icon_path'], app.ICO)

    def test_on_done_remembers_device_with_name(self):
        """连接成功后：串号+名字写进配置并落盘，状态复位。"""
        app.open_connect_wizard()
        self._wiz_kwargs['on_done']('192.168.1.5:41001', '一加 13')
        self.assertEqual(app.CFG['serial'], '192.168.1.5:41001')
        known = app.CFG['known_devices']
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]['name'], '一加 13')
        self.assertEqual(known[0]['addr'], '192.168.1.5:41001')
        self.assertTrue(self._save_calls, 'save_cfg 未被调用')
        self.assertFalse(app.state['wizard_open'])
        self.assertEqual(app.state['retries'], 0)
        self.assertFalse(app.state['error_shown'])

    def test_on_done_without_name_still_works(self):
        """旧式调用 on_done(serial) 不带名字也不能崩（name 有默认值）。"""
        app.open_connect_wizard()
        self._wiz_kwargs['on_done']('ABC123')
        self.assertEqual(app.CFG['serial'], 'ABC123')
        self.assertEqual(app.CFG['known_devices'][0]['serial'], 'ABC123')

    def test_on_close_clears_flag(self):
        """直接关向导要清 wizard_open，否则托盘菜单再点会被当重复请求。"""
        app.open_connect_wizard()
        self.assertTrue(app.state['wizard_open'])
        self._wiz_kwargs['on_close']()
        self.assertFalse(app.state['wizard_open'])


if __name__ == '__main__':
    unittest.main()