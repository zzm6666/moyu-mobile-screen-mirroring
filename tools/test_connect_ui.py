"""connect_ui 里纯逻辑的单元测试（不弹窗、不调真 adb）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connect_ui as c


class TestNormalizeIpPort(unittest.TestCase):
    def test_accepts_ip_port(self):
        self.assertEqual(c.normalize_ip_port('192.168.137.198:43847'), '192.168.137.198:43847')
        self.assertEqual(c.normalize_ip_port('  10.0.0.2:1  '), '10.0.0.2:1')

    def test_rejects_missing_port_or_garbage(self):
        self.assertEqual(c.normalize_ip_port('192.168.137.198'), '')
        self.assertEqual(c.normalize_ip_port('192.168.137.198:'), '')
        self.assertEqual(c.normalize_ip_port('adb-bfd7e543-MoDo9u._adb-tls-connect._tcp'), '')
        self.assertEqual(c.normalize_ip_port(''), '')
        self.assertEqual(c.normalize_ip_port(None), '')


class TestClassifyAndCode(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(c.classify_device('192.168.1.5:5555'), 'wifi')
        self.assertEqual(c.classify_device('adb-bfd7e543-MoDo9u._adb-tls-connect._tcp'), 'usb')
        self.assertEqual(c.classify_device('R58M123456X'), 'usb')
        self.assertEqual(c.classify_device(''), 'usb')

    def test_valid_code(self):
        self.assertTrue(c.valid_code('835165'))
        self.assertTrue(c.valid_code(' 608474 '))
        self.assertFalse(c.valid_code('83516'))
        self.assertFalse(c.valid_code('83516a'))
        self.assertFalse(c.valid_code(''))


class TestPairThenConnect(unittest.TestCase):
    def _runner(self, table):
        calls = []

        def run(cmd, timeout=25):
            calls.append(cmd)
            key = ' '.join(cmd[1:3])
            return 0, table.get(key, '')
        run.calls = calls
        return run

    def test_pair_then_devices_shows_up(self):
        """配对后 mDNS 设备直接出现在 adb devices 里 → 直接用。"""
        run = self._runner({'pair 192.168.1.5:43847': 'Successfully paired to 192.168.1.5:43847 [guid=adb-x]',
                            'devices': ('List of devices attached\n'
                                        'adb-xxx._adb-tls-connect._tcp\tdevice\n\n')})
        ok, msg, serial = c.pair_then_connect('adb.exe', '192.168.1.5:43847', '835165', run)
        self.assertTrue(ok, msg)
        self.assertEqual(serial, 'adb-xxx._adb-tls-connect._tcp')
        self.assertEqual(run.calls[0][1:], ['pair', '192.168.1.5:43847', '835165'])

    def test_pair_then_mdns_port(self):
        """devices 里还没有 → 查 adb mdns services 拿到**连接端口**再 connect。"""
        run = self._runner({
            'pair 192.168.1.5:43847': 'Successfully paired',
            'devices': 'List of devices attached\n\n',
            'mdns services': ('List of discovered mdns services\n'
                              'adb-xxx\t_adb-tls-connect._tcp.\t192.168.1.5:41001\n'),
            'connect 192.168.1.5:41001': 'connected to 192.168.1.5:41001',
        })
        ok, msg, serial = c.pair_then_connect('adb.exe', '192.168.1.5:43847', '835165', run)
        self.assertTrue(ok, msg)
        self.assertEqual(serial, '192.168.1.5:41001')

    def test_pair_failed(self):
        run = self._runner({'pair 192.168.1.5:43847': 'Failed: Wrong password or connection was dropped.'})
        ok, msg, serial = c.pair_then_connect('adb.exe', '192.168.1.5:43847', '000000', run)
        self.assertFalse(ok)
        self.assertIn('Failed', msg)
        self.assertEqual(serial, '')

    def test_pair_ok_but_no_connect_port(self):
        """配对成功却找不到连接端口 → 明确告诉用户去填「IP 地址和端口」。"""
        run = self._runner({'pair 192.168.1.5:43847': 'Successfully paired',
                            'devices': 'List of devices attached\n\n',
                            'mdns services': 'List of discovered mdns services\n'})
        ok, msg, serial = c.pair_then_connect('adb.exe', '192.168.1.5:43847', '835165', run)
        self.assertFalse(ok)
        self.assertIn('连接端口', msg)
        self.assertEqual(serial, '')

    def test_bad_input_does_not_call_adb(self):
        run = self._runner({})
        ok, msg, _ = c.pair_then_connect('adb.exe', '192.168.1.5', '835165', run)
        self.assertFalse(ok)
        self.assertEqual(run.calls, [])
        ok, msg, _ = c.pair_then_connect('adb.exe', '192.168.1.5:1', '12345', run)
        self.assertFalse(ok)
        self.assertEqual(run.calls, [])


class TestConnectOnly(unittest.TestCase):
    def test_success(self):
        def run(cmd, timeout=25):
            return 0, 'connected to 192.168.1.5:41001'
        ok, msg, serial = c.connect_only('adb.exe', '192.168.1.5:41001', run)
        self.assertTrue(ok, msg)
        self.assertEqual(serial, '192.168.1.5:41001')

    def test_failure(self):
        def run(cmd, timeout=25):
            return 0, 'cannot connect to 192.168.1.5:41001: 由于目标计算机积极拒绝'
        ok, msg, serial = c.connect_only('adb.exe', '192.168.1.5:41001', run)
        self.assertFalse(ok)
        self.assertIn('连接失败', msg)
        self.assertEqual(serial, '')

    def test_bad_input(self):
        def run(cmd, timeout=25):
            raise AssertionError('不该调用 adb')
        ok, msg, serial = c.connect_only('adb.exe', '192.168.1.5', run)
        self.assertFalse(ok)
        self.assertEqual(serial, '')


class TestTryAutoConnect(unittest.TestCase):
    """启动时的自动查找：在线设备 → 上次的 IP:端口 → mDNS。"""

    def _runner(self, table):
        calls = []

        def run(cmd, timeout=25):
            calls.append(cmd)
            key = ' '.join(cmd[1:3])
            return 0, table.get(key, '')
        run.calls = calls
        return run

    def test_uses_online_device_first(self):
        run = self._runner({'devices': 'List of devices attached\nR58M1\tdevice\n'})
        self.assertEqual(c.try_auto_connect('adb.exe', '', run), 'R58M1')

    def test_reconnects_last_ip_port(self):
        run = self._runner({'devices': 'List of devices attached\n\n',
                            'connect 192.168.1.5:43773': 'connected to 192.168.1.5:43773',
                            'mdns services': ''})
        self.assertEqual(c.try_auto_connect('adb.exe', '192.168.1.5:43773', run),
                         '192.168.1.5:43773')

    def test_falls_back_to_mdns(self):
        run = self._runner({'devices': 'List of devices attached\n\n',
                            'connect 192.168.1.5:41001': 'connected to 192.168.1.5:41001',
                            'mdns services': ('List of discovered mdns services\n'
                                              'adb-x\t_adb-tls-connect._tcp.\t192.168.1.5:41001\n')})
        self.assertEqual(c.try_auto_connect('adb.exe', '192.168.1.5:43773', run),
                         '192.168.1.5:41001')

    def test_nothing_found(self):
        run = self._runner({'devices': 'List of devices attached\n\n',
                            'mdns services': 'List of discovered mdns services\n'})
        self.assertEqual(c.try_auto_connect('adb.exe', '192.168.1.5:43773', run), '')


class TestParseMdns(unittest.TestCase):
    def test_picks_matching_ip(self):
        text = ('List of discovered mdns services\n'
                'adb-a\t_adb-tls-connect._tcp.\t192.168.1.9:40001\n'
                'adb-b\t_adb-tls-connect._tcp.\t192.168.1.5:41001\n')
        self.assertEqual(c.parse_mdns_services(text, '192.168.1.5'), '192.168.1.5:41001')

    def test_no_match(self):
        text = 'List of discovered mdns services\nadb-b\t_adb-tls-connect._tcp.\t192.168.1.5:41001\n'
        self.assertEqual(c.parse_mdns_services(text, '10.0.0.9'), '')
        self.assertEqual(c.parse_mdns_services('', '192.168.1.5'), '')


class TestListOnline(unittest.TestCase):
    def test_only_device_state(self):
        def run(cmd, timeout=25):
            return 0, ('List of devices attached\n'
                       'R58M123456X\tdevice\n'
                       'OFFLINE1\toffline\n'
                       '192.168.1.5:5555\tdevice\n\n')
        self.assertEqual(c.list_online('adb.exe', run), ['R58M123456X', '192.168.1.5:5555'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
