# -*- coding: utf-8 -*-
"""connect_ui.py 逻辑层离线单元测试（全部 adb 调用走假 runner，绝不真跑 adb）。

覆盖：friendly_error / normalize_ip_port / valid_code / classify_device /
parse_devices_l / parse_mdns_all / parse_mdns_services / merge_devices /
reconnect_known / pair_then_connect / connect_only / device_display_name /
remember_device / try_auto_connect。

运行（workdir=D:\\moyu\\tools）：
  & D:\\moyu\\.venv\\Scripts\\python.exe -X utf8 -m unittest test_connect_ui -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import connect_ui as cu

ADB = 'adb.exe'

EMPTY_DEVICES = 'List of devices attached\n'

# 任务给定的真实格式 `adb devices -l` 文本
DEVICES_L_TEXT = (
    'List of devices attached\n'
    'adb-bfd7e543-MoDo9u._adb-tls-connect._tcp device product:PJZ110 '
    'model:PJZ110 device:OP5D0DL1 transport_id:4\n'
    '192.168.1.5:5555 device model:Pixel_7\n'
    'ABC123 unauthorized\n'
)

# 任务给定的真实格式 `adb mdns services` 文本
MDNS_TEXT = (
    'List of discovered mdns services\n'
    'adb-bfd7e543-MoDo9u\t_adb-tls-connect._tcp.\t192.168.137.198:41001\n'
    'adb-009b17d0\t_adb._tcp\t20.30.1.217:5555\n'
)

MDNS_SERIAL = 'adb-bfd7e543-MoDo9u._adb-tls-connect._tcp'


def mdns_text(*rows):
    """拼一段 `adb mdns services` 输出；rows 为 (实例名, 服务类型, ip:port)。"""
    lines = ['List of discovered mdns services']
    for name, svc, addr in rows:
        lines.append('%s\t%s\t%s' % (name, svc, addr))
    return '\n'.join(lines) + '\n'


def devices_text(*serials):
    """拼一段 `adb devices` 输出（全部 device 状态）。"""
    lines = ['List of devices attached']
    for s in serials:
        lines.append('%s\tdevice' % s)
    return '\n'.join(lines) + '\n'


class FakeRunner:
    """假 adb 执行器：按 cmd 内容分发罐头响应，并记录全部调用。

    签名与真 runner 一致：runner(cmd, timeout=25) -> (returncode, text)。
    connect_map 按目标 ip:port 精确分发；未命中用 connect_default。
    """

    def __init__(self, devices=EMPTY_DEVICES, mdns='',
                 pair_result=(0, 'Successfully paired to 192.168.1.5:43847'),
                 connect_map=None, connect_default=(1, 'failed to connect'),
                 shell_out=''):
        self.devices = devices
        self.mdns = mdns
        self.pair_result = pair_result
        self.connect_map = connect_map or {}
        self.connect_default = connect_default
        self.shell_out = shell_out
        self.calls = []

    def __call__(self, cmd, timeout=25):
        self.calls.append((list(cmd), timeout))
        a = list(cmd[1:])
        if a and a[0] == 'devices':
            return 0, self.devices
        if a[:2] == ['mdns', 'services']:
            return 0, self.mdns
        if a and a[0] == 'pair':
            return self.pair_result
        if a and a[0] == 'connect':
            return self.connect_map.get(a[1], self.connect_default)
        if 'shell' in a:
            return 0, self.shell_out
        raise AssertionError('FakeRunner 收到未预料的命令: %r' % (cmd,))

    def cmds(self, verb):
        """取出某类命令的参数部分（去掉 adb 本身）。"""
        return [c[1:] for c, _ in self.calls if c[1:2] == [verb]]


class ChineseMsgMixin:
    """断言提示是通俗中文，且不泄漏原始英文片段。"""

    def assert_plain_chinese(self, msg, *forbidden):
        self.assertIsInstance(msg, str)
        self.assertTrue(msg, '提示不应为空')
        self.assertTrue(any('\u4e00' <= ch <= '\u9fff' for ch in msg),
                        '提示应含中文: %r' % msg)
        self.assertNotIn('error:', msg.lower())
        for frag in forbidden:
            self.assertNotIn(frag.lower(), msg.lower(),
                             '提示不应含原始英文片段 %r: %r' % (frag, msg))


class FriendlyErrorTest(ChineseMsgMixin, unittest.TestCase):
    def test_10061_means_wrong_port(self):
        """10061（连接被拒）→ 提示端口不对，让用户看无线调试主页面。"""
        raw = ('failed to connect to 192.168.1.5:41001: '
               'Connection refused (10061)')
        msg = cu.friendly_error(raw, '连接')
        self.assertIn('端口不对', msg)
        self.assert_plain_chinese(msg, '10061', 'failed to connect',
                                  'connection refused')

    def test_protocol_fault_means_pairing_expired(self):
        """protocol fault → 配对码/配对端口过期，引导重新开配对弹窗。"""
        raw = "error: protocol fault (couldn't read status message)"
        msg = cu.friendly_error(raw, '配对')
        self.assertIn('过期', msg)
        self.assert_plain_chinese(msg, 'protocol fault', "couldn't read")

    def test_protocol_fault_case_insensitive(self):
        """原始输出大小写不敏感，大写 PROTOCOL FAULT 也要命中。"""
        msg = cu.friendly_error('PROTOCOL FAULT (status 0)', '配对')
        self.assertIn('过期', msg)

    def test_unauthorized_means_phone_approval(self):
        """unauthorized → 提示在手机上点允许授权。"""
        msg = cu.friendly_error('error: device unauthorized.', '连接')
        self.assertIn('授权', msg)
        self.assert_plain_chinese(msg, 'unauthorized')

    def test_empty_output_fallback(self):
        """空输出/None → 兜底中文，带上动作名。"""
        for raw in ('', '   \n', None):
            msg = cu.friendly_error(raw, '连接')
            self.assert_plain_chinese(msg)
            self.assertIn('连接没有成功', msg)

    def test_action_is_inserted(self):
        """兜底文案里的动作名来自 action 参数。"""
        msg = cu.friendly_error('', '配对')
        self.assertTrue(msg.startswith('配对没有成功'))

    def test_unknown_english_fallback_has_no_raw_text(self):
        """未知英文报错 → 兜底中文，且不含原始英文句子。"""
        raw = 'error: some weird unknown failure happened'
        msg = cu.friendly_error(raw, '连接')
        self.assert_plain_chinese(msg, 'weird', 'unknown failure', 'happened')
        self.assertNotIn(raw, msg)

    def test_10060_timeout_hint(self):
        """10060 → 超时/同一 Wi-Fi 提示。"""
        msg = cu.friendly_error('connect failed: 10060', '连接')
        self.assertIn('超时', msg)
        self.assert_plain_chinese(msg, '10060')


class ValidationTest(unittest.TestCase):
    def test_normalize_ip_port_valid(self):
        """标准 IP:端口 合法，首尾空白被去掉。"""
        self.assertEqual(cu.normalize_ip_port('192.168.1.5:41001'),
                         '192.168.1.5:41001')
        self.assertEqual(cu.normalize_ip_port('  192.168.1.5:41001 \n'),
                         '192.168.1.5:41001')

    def test_normalize_ip_port_invalid(self):
        """缺端口、缺 IP、空串、非法字符一律返回空串。"""
        for bad in ('192.168.1.5', '192.168.1.5:', ':41001', '', None,
                    'abc:41001', '192.168.1.5:41001:2', '192.168.1.5 41001'):
            self.assertEqual(cu.normalize_ip_port(bad), '', repr(bad))

    def test_valid_code_six_digits(self):
        """配对码必须是 6 位数字（允许首尾空白）。"""
        self.assertTrue(cu.valid_code('123456'))
        self.assertTrue(cu.valid_code(' 042917 '))
        for bad in ('12345', '1234567', '12345a', 'abcdef', '', None, '12 345'):
            self.assertFalse(cu.valid_code(bad), repr(bad))

    def test_join_ip_port_two_fields(self):
        """IP 和端口分两个输入框：程序拼冒号，用户不用输「:」。"""
        self.assertEqual(cu.join_ip_port('192.168.1.5', '43847'),
                         '192.168.1.5:43847')
        self.assertEqual(cu.join_ip_port(' 192.168.1.5 ', ' 43847 '),
                         '192.168.1.5:43847')
        # IP 框里粘贴整串「IP:端口」也认（端口框留空）
        self.assertEqual(cu.join_ip_port('192.168.1.5:43847', ''),
                         '192.168.1.5:43847')
        # IP 框带冒号时以端口框为准
        self.assertEqual(cu.join_ip_port('192.168.1.5:11111', '43847'),
                         '192.168.1.5:43847')

    def test_join_ip_port_invalid(self):
        """缺 IP、缺端口、非法字符 → 空串。"""
        for ip, port in (('', '43847'), ('192.168.1.5', ''), ('', ''),
                         ('abc', '43847'), ('192.168.1.5', '43a47'),
                         ('192.168.1.5', '438 47'), (None, None)):
            self.assertEqual(cu.join_ip_port(ip, port), '', repr((ip, port)))

    def test_classify_device(self):
        """含 ':' 或 '_tcp' → wifi；纯字母数字串号 → usb。"""
        self.assertEqual(cu.classify_device('192.168.1.5:5555'), 'wifi')
        self.assertEqual(cu.classify_device(MDNS_SERIAL), 'wifi')
        self.assertEqual(cu.classify_device('foo_tcp_bar'), 'wifi')
        self.assertEqual(cu.classify_device('ABC123'), 'usb')
        self.assertEqual(cu.classify_device(''), 'usb')
        self.assertEqual(cu.classify_device(None), 'usb')


class ParseDevicesLTest(unittest.TestCase):
    def test_given_real_text(self):
        """真实格式文本：只收 device 状态的两台，model/product/transport_id 正确。"""
        devs = cu.parse_devices_l(DEVICES_L_TEXT)
        self.assertEqual(len(devs), 2)
        self.assertEqual(devs[0]['serial'], MDNS_SERIAL)
        self.assertEqual(devs[0]['model'], 'PJZ110')
        self.assertEqual(devs[0]['product'], 'PJZ110')
        self.assertEqual(devs[0]['transport_id'], '4')
        self.assertEqual(devs[1]['serial'], '192.168.1.5:5555')
        self.assertEqual(devs[1]['model'], 'Pixel_7')
        self.assertNotIn('ABC123', [d['serial'] for d in devs])

    def test_empty_and_header_only(self):
        """空文本/只有表头 → 空列表。"""
        self.assertEqual(cu.parse_devices_l(''), [])
        self.assertEqual(cu.parse_devices_l(None), [])
        self.assertEqual(cu.parse_devices_l('List of devices attached\n'), [])

    def test_offline_and_unauthorized_excluded(self):
        """offline / unauthorized 状态都不算在线。"""
        text = ('List of devices attached\n'
                'AAA111 offline\nBBB222 unauthorized\nCCC333 device\n')
        devs = cu.parse_devices_l(text)
        self.assertEqual([d['serial'] for d in devs], ['CCC333'])

    def test_list_devices_detailed_uses_runner(self):
        """list_devices_detailed 走注入 runner 的 `devices -l`。"""
        r = FakeRunner(devices=DEVICES_L_TEXT)
        devs = cu.list_devices_detailed(ADB, r)
        self.assertEqual(len(devs), 2)
        self.assertEqual(r.calls[0][0], [ADB, 'devices', '-l'])


class ParseMdnsTest(unittest.TestCase):
    def test_parse_mdns_all_given_text(self):
        """只取 _adb-tls-connect 服务；_adb._tcp（旧版无线调试）不要。"""
        out = cu.parse_mdns_all(MDNS_TEXT)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'adb-bfd7e543-MoDo9u')
        self.assertEqual(out[0]['serial'], MDNS_SERIAL)
        self.assertEqual(out[0]['addr'], '192.168.137.198:41001')

    def test_parse_mdns_all_skips_bad_addr(self):
        """addr 不是 IP:端口 的行丢弃；空文本 → 空列表。"""
        text = mdns_text(('adb-aa', '_adb-tls-connect._tcp.', 'not-an-addr'),
                         ('adb-bb', '_adb-tls-connect._tcp.', '10.0.0.2:5555'))
        out = cu.parse_mdns_all(text)
        self.assertEqual([d['addr'] for d in out], ['10.0.0.2:5555'])
        self.assertEqual(cu.parse_mdns_all(''), [])
        self.assertEqual(cu.parse_mdns_all(None), [])

    def test_parse_mdns_services_no_filter(self):
        """不带 IP 过滤 → 返回第一个 tls-connect 服务地址。"""
        self.assertEqual(cu.parse_mdns_services(MDNS_TEXT),
                         '192.168.137.198:41001')

    def test_parse_mdns_services_ip_filter(self):
        """按 IP 过滤：命中返回地址；该 IP 只有 _adb._tcp 服务时返回空。"""
        self.assertEqual(cu.parse_mdns_services(MDNS_TEXT, '192.168.137.198'),
                         '192.168.137.198:41001')
        self.assertEqual(cu.parse_mdns_services(MDNS_TEXT, '20.30.1.217'), '')
        self.assertEqual(cu.parse_mdns_services(MDNS_TEXT, '10.9.9.9'), '')

    def test_parse_mdns_services_ip_prefix_not_substring(self):
        """IP 过滤必须按「IP:」前缀匹配，不能误配 192.168.137.19 这种前缀。"""
        self.assertEqual(cu.parse_mdns_services(MDNS_TEXT, '192.168.137.19'), '')

    def test_parse_mdns_services_empty(self):
        """空文本 → 空串。"""
        self.assertEqual(cu.parse_mdns_services(''), '')
        self.assertEqual(cu.parse_mdns_services(None, '1.2.3.4'), '')


class EnrichMdnsTest(unittest.TestCase):
    """_enrich_mdns：用自带 mDNS 浏览器给发现行补地址（去重才有据可依）。"""

    class _Browser:
        def __init__(self, items):
            self.items = items
            self.calls = []

        def browse(self, service_type, timeout=4.0, **kw):
            self.calls.append((service_type, timeout))
            return list(self.items)

    def test_fills_addr_for_known_serial(self):
        discovered = [{'name': 'adb-bfd7e543-MoDo9u', 'serial': MDNS_SERIAL, 'addr': ''}]
        browser = self._Browser([{'name': MDNS_SERIAL + '.local', 'port': 45197,
                                  'addr': '192.168.137.144'}])
        items = cu._enrich_mdns(discovered, browser=browser)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['addr'], '192.168.137.144:45197')
        self.assertEqual(browser.calls[0][0], '_adb-tls-connect._tcp')

    def test_adds_instance_adb_did_not_list(self):
        items = cu._enrich_mdns([], browser=self._Browser(
            [{'name': 'adb-009b17d0._adb-tls-connect._tcp.local', 'port': 41234,
              'addr': '192.168.137.88'}]))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['serial'], 'adb-009b17d0._adb-tls-connect._tcp')
        self.assertEqual(items[0]['addr'], '192.168.137.88:41234')

    def test_ignores_records_without_usable_addr(self):
        items = cu._enrich_mdns([], browser=self._Browser(
            [{'name': 'adb-x._adb-tls-connect._tcp.local', 'port': 0, 'addr': ''}]))
        self.assertEqual(items, [])

    def test_no_browser_returns_copy(self):
        discovered = [{'name': 'a', 'serial': 'a', 'addr': '192.168.1.9:5555'}]
        items = cu._enrich_mdns(discovered, browser=False)
        self.assertEqual(items, discovered)
        self.assertIsNot(items[0], discovered[0], '应返回副本，别改调用方数据')

    def test_browser_failure_is_silent(self):
        class Boom:
            def browse(self, *a, **kw):
                raise OSError('no socket')

        self.assertEqual(cu._enrich_mdns([{'serial': 'a', 'name': 'a', 'addr': ''}],
                                         browser=Boom())[0]['serial'], 'a')


class MergeDevicesTest(unittest.TestCase):
    def test_mdns_online_row_and_same_phone_found_row_merge_into_one(self):
        """用户实测的重复：一台手机同时在线的 mDNS 串号 + 新发现的 ip:port 显示成两行。"""
        online = [{'serial': MDNS_SERIAL, 'model': 'PJZ110'}]
        discovered = [{'name': 'adb-bfd7e543-MoDo9u',
                       'serial': MDNS_SERIAL,
                       'addr': '192.168.137.144:36183'}]
        rows = cu.merge_devices(online, discovered, [])
        self.assertEqual(len(rows), 1, '同一台手机不该显示两行：%r' % rows)
        self.assertEqual(rows[0]['state'], 'online')
        # 在线行也要带上 IP:端口，否则用户看不出是哪台
        self.assertEqual(rows[0]['addr'], '192.168.137.144:36183')

    def test_two_online_serials_same_ip_merge_into_one(self):
        """adb 同时报了 ip:port 和 mDNS 串号（同一台机）：只显示一行。"""
        online = [{'serial': MDNS_SERIAL, 'model': 'PJZ110'},
                  {'serial': '192.168.137.144:36183', 'model': 'PJZ110'}]
        discovered = [{'name': 'adb-bfd7e543-MoDo9u', 'serial': MDNS_SERIAL,
                       'addr': '192.168.137.144:36183'}]
        rows = cu.merge_devices(online, discovered, [])
        self.assertEqual(len(rows), 1, '同一台手机不该显示两行：%r' % rows)
        self.assertEqual(rows[0]['state'], 'online')

    def test_two_online_serials_without_mdns_addr_merge_into_one(self):
        """拿不到 mDNS 地址时（缓存为空）也要并成一行，并补上地址给用户看。"""
        online = [{'serial': MDNS_SERIAL, 'model': 'PJZ110'},
                  {'serial': '192.168.137.144:45197', 'model': 'PJZ110'}]
        rows = cu.merge_devices(online, [], [])
        self.assertEqual(len(rows), 1, '同一台手机不该显示两行：%r' % rows)
        self.assertEqual(rows[0]['serial'], MDNS_SERIAL)
        self.assertEqual(rows[0]['addr'], '192.168.137.144:45197')

    def test_usb_and_wifi_rows_stay_separate(self):
        """USB 一台 + wifi 一台：两行都要留（不能把不同手机并掉）。"""
        online = [{'serial': 'ABC123', 'model': 'Pixel_7'},
                  {'serial': MDNS_SERIAL, 'model': 'PJZ110'}]
        rows = cu.merge_devices(online, [], [])
        self.assertEqual(len(rows), 2, '不该并成一行：%r' % rows)

    def test_remembered_name_prefers_known_then_ip(self):
        """没连上的设备用记住的名字，其次「无线设备 IP」，绝不显示 MoDo9u。"""
        known = [{'serial': MDNS_SERIAL, 'name': '一加 13', 'addr': ''}]
        row = {'serial': MDNS_SERIAL, 'addr': '', 'name': 'adb-bfd7e543-MoDo9u'}
        self.assertEqual(cu._remembered_name(row, known), '一加 13')
        row2 = {'serial': '192.168.137.144:36183',
                'addr': '192.168.137.144:36183', 'name': 'adb-xx'}
        self.assertEqual(cu._remembered_name(row2, []), '无线设备 192.168.137.144')

    def test_order_and_fields(self):
        """三源排序 online > found > offline；kind/state/name/addr 字段正确。"""
        online = [{'serial': 'ABC123', 'model': 'Pixel_7', 'product': '',
                   'transport_id': '1'}]
        discovered = [{'name': 'adb-009b17d0', 'serial': 'adb-009b17d0._adb-tls-connect._tcp',
                       'addr': '192.168.1.9:41001'}]
        known = [{'serial': 'XYZ789', 'name': '旧手机', 'addr': ''}]
        rows = cu.merge_devices(online, discovered, known)
        self.assertEqual([r['state'] for r in rows], ['online', 'found', 'offline'])
        self.assertEqual(rows[0]['kind'], 'usb')
        self.assertEqual(rows[0]['name'], 'Pixel_7')
        self.assertEqual(rows[0]['addr'], '')
        self.assertEqual(rows[1]['kind'], 'wifi')
        self.assertEqual(rows[1]['serial'], '192.168.1.9:41001')
        self.assertEqual(rows[1]['addr'], '192.168.1.9:41001')
        self.assertEqual(rows[2]['kind'], 'usb')
        self.assertEqual(rows[2]['name'], '旧手机')

    def test_legacy_known_ipport_and_mdns_row_merge_into_one(self):
        """旧配置里同时留着 ip:port 和 mDNS 串号两条历史记录：列表只显示一行。"""
        known = [
            {'serial': '192.168.137.144:45197', 'name': '192.168.137.144:45197',
             'addr': '192.168.137.144:45197', 'last_seen': 2},
            {'serial': MDNS_SERIAL, 'name': MDNS_SERIAL, 'addr': '', 'last_seen': 1},
        ]
        rows = cu.merge_devices([], [], known)
        self.assertEqual(len(rows), 1, '同一台手机不该显示两行：%r' % rows)
        self.assertEqual(rows[0]['serial'], MDNS_SERIAL)
        # 名字不能是串号
        self.assertNotIn('adb-', rows[0]['name'])
        self.assertNotIn('192.168.137.144:45197', rows[0]['name'])

    def test_two_different_phones_in_known_stay_two_rows(self):
        """历史里两台不同的 wifi 手机：不能并成一行。"""
        known = [
            {'serial': '192.168.1.5:41001', 'name': '手机A', 'addr': '192.168.1.5:41001'},
            {'serial': MDNS_SERIAL, 'name': '手机B', 'addr': '192.168.1.9:41001'},
        ]
        rows = cu.merge_devices([], [], known)
        self.assertEqual(len(rows), 2, '两台不同手机不该合并：%r' % rows)

    def test_online_ipport_dedups_discovered_same_addr(self):
        """在线串号就是 ip:port 时，discovered 里同地址的 mDNS 行不重复。"""
        online = [{'serial': '192.168.1.5:5555', 'model': 'Pixel_7'}]
        discovered = [{'name': 'adb-xx', 'serial': 'adb-xx._adb-tls-connect._tcp',
                       'addr': '192.168.1.5:5555'}]
        rows = cu.merge_devices(online, discovered, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'online')

    def test_known_same_ip_diff_port_replaced_by_online(self):
        """同 IP 不同端口的 known 历史行被在线行取代（端口变了不重复列）。"""
        online = [{'serial': '192.168.1.5:5555', 'model': 'Pixel_7'}]
        known = [{'serial': '192.168.1.5:41001', 'name': '一加 13',
                  'addr': '192.168.1.5:41001'}]
        rows = cu.merge_devices(online, [], known)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'online')
        self.assertEqual(rows[0]['serial'], '192.168.1.5:5555')

    def test_mdns_online_dedups_discovered_same_serial(self):
        """mDNS 串号在线时，discovered 里同一台（同串号）不重复。"""
        online = [{'serial': MDNS_SERIAL, 'model': 'PJZ110'}]
        discovered = [{'name': 'adb-bfd7e543-MoDo9u', 'serial': MDNS_SERIAL,
                       'addr': '192.168.137.198:41001'}]
        rows = cu.merge_devices(online, discovered, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'online')

    def test_mdns_online_dedups_known_ipport(self):
        """同一台手机以 mDNS 串号在线、又以 ip:port 记在 known 里：不应重复。

        discovered 提供了「mDNS 串号 ↔ ip:port」的对应关系，known 里的
        ip:port 历史行应被在线行取代。
        """
        online = [{'serial': MDNS_SERIAL, 'model': 'PJZ110'}]
        discovered = [{'name': 'adb-bfd7e543-MoDo9u', 'serial': MDNS_SERIAL,
                       'addr': '192.168.137.198:41001'}]
        known = [{'serial': '192.168.137.198:41001', 'name': '一加 13',
                  'addr': '192.168.137.198:41001'}]
        rows = cu.merge_devices(online, discovered, known)
        self.assertEqual(len(rows), 1, '同一台手机不应出现两行: %r' % rows)
        self.assertEqual(rows[0]['state'], 'online')

    def test_empty_sources(self):
        """三源全空 → 空列表；known 为 None 也不炸。"""
        self.assertEqual(cu.merge_devices([], [], []), [])
        self.assertEqual(cu.merge_devices([], [], None), [])

    def test_known_offline_wifi_fields(self):
        """历史无线设备离线行：kind=wifi、state=offline、addr 保留。"""
        known = [{'serial': '192.168.1.5:41001', 'name': '一加 13',
                  'addr': '192.168.1.5:41001'}]
        rows = cu.merge_devices([], [], known)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'wifi')
        self.assertEqual(rows[0]['state'], 'offline')
        self.assertEqual(rows[0]['name'], '一加 13')


class ReconnectKnownTest(ChineseMsgMixin, unittest.TestCase):
    def test_usb_online_direct_success(self):
        """USB 设备已在线 → 直接成功，不做任何 connect。"""
        r = FakeRunner(devices=devices_text('ABC123'))
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': 'ABC123', 'addr': '', 'name': '旧手机'}, r)
        self.assertTrue(ok)
        self.assertEqual(serial, 'ABC123')
        self.assertEqual(msg, '连接成功')
        self.assertEqual(r.cmds('connect'), [])

    def test_usb_offline_chinese_hint(self):
        """USB 设备不在线 → 中文提示插线/授权，不尝试无线连接。"""
        r = FakeRunner()
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': 'ABC123', 'addr': '', 'name': '旧手机'}, r)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assert_plain_chinese(msg)
        self.assertIn('USB', msg)
        self.assertEqual(r.cmds('connect'), [])
        self.assertEqual(r.cmds('mdns'), [])

    def test_wifi_old_port_still_works(self):
        """无线历史设备：旧端口没变，connect 旧端口直接成功。"""
        r = FakeRunner(connect_map={
            '192.168.1.5:5555': (0, 'connected to 192.168.1.5:5555')})
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': '192.168.1.5:5555', 'addr': '192.168.1.5:5555',
                  'name': 'Pixel'}, r)
        self.assertTrue(ok)
        self.assertEqual(serial, '192.168.1.5:5555')
        self.assertEqual(r.cmds('connect'), [['connect', '192.168.1.5:5555']])
        # 成功后要问一次 adb 到底认哪个串号（ip:port 可能已被升级成 mDNS 串号）
        self.assertTrue(all(c[:2] == ['mdns', 'services'] for c in r.cmds('mdns')),
                        '成功后只允许查 mDNS 确认串号：%r' % r.cmds('mdns'))

    def test_wifi_mdns_finds_new_port(self):
        """旧端口失败 → mDNS 找到新端口 → connect 新端口成功。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-bfd7e543-MoDo9u', '_adb-tls-connect._tcp.',
                            '192.168.1.5:41001')),
            connect_map={
                '192.168.1.5:5555': (1, 'failed to connect: 10061'),
                '192.168.1.5:41001': (0, 'connected to 192.168.1.5:41001')})
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': '192.168.1.5:5555', 'addr': '192.168.1.5:5555',
                  'name': 'Pixel'}, r)
        self.assertTrue(ok)
        self.assertEqual(serial, '192.168.1.5:41001')
        self.assertEqual(r.cmds('connect'),
                         [['connect', '192.168.1.5:5555'],
                          ['connect', '192.168.1.5:41001']])

    def test_wifi_mdns_serial_still_online(self):
        """历史记录的串号是 mDNS 串号且仍在线 → 直接用，不 connect。"""
        r = FakeRunner(devices=devices_text(MDNS_SERIAL))
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': MDNS_SERIAL, 'addr': '192.168.137.198:41001',
                  'name': '一加 13'}, r)
        self.assertTrue(ok)
        self.assertEqual(serial, MDNS_SERIAL)
        self.assertEqual(r.cmds('connect'), [])

    def test_wifi_all_fail_chinese_hint(self):
        """旧端口失败、mDNS 也没有 → 中文提示，串号为空。"""
        r = FakeRunner(mdns='List of discovered mdns services\n')
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': '192.168.1.5:5555', 'addr': '192.168.1.5:5555',
                  'name': 'Pixel'}, r)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assert_plain_chinese(msg, 'failed to connect', '10061')
        self.assertIn('无线调试', msg)

    def test_wifi_mdns_same_name_new_ip(self):
        """手机换了 IP（DHCP 变了）→ 按 mDNS 服务名认回来，连新地址。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-bfd7e543-MoDo9u', '_adb-tls-connect._tcp.',
                            '192.168.137.72:45861')),
            connect_map={
                '192.168.137.198:41001': (1, 'failed to connect: 10061'),
                '192.168.137.72:45861': (0, 'connected to 192.168.137.72:45861')})
        ok, msg, serial = cu.reconnect_known(
            ADB, {'serial': MDNS_SERIAL, 'addr': '192.168.137.198:41001',
                  'name': '一加 13'}, r)
        self.assertTrue(ok)
        self.assertEqual(serial, '192.168.137.72:45861')


class PairThenConnectTest(ChineseMsgMixin, unittest.TestCase):
    def test_pair_fail_returns_chinese_error(self):
        """配对失败 → 直接返回中文错误，不再查 devices/mdns。"""
        r = FakeRunner(pair_result=(1, "error: protocol fault (couldn't read)"))
        ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847',
                                               '123456', r)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assert_plain_chinese(msg, 'protocol fault')
        self.assertIn('过期', msg)
        self.assertEqual(r.cmds('devices'), [])
        self.assertEqual(r.cmds('mdns'), [])

    def test_pair_ok_device_already_online(self):
        """配对成功后 adb devices 已有设备 → 成功，返回该串号。"""
        r = FakeRunner(devices=devices_text(MDNS_SERIAL))
        ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847',
                                               '123456', r)
        self.assertTrue(ok)
        self.assertEqual(serial, MDNS_SERIAL)
        self.assertEqual(msg, '配对并连接成功')
        self.assertEqual(r.cmds('connect'), [])

    def test_pair_ok_mdns_connect_port(self):
        """devices 为空但 mDNS 有连接端口 → connect 该端口成功。

        关键：connect 的必须是 mDNS 给出的连接端口，而不是配对端口。
        """
        r = FakeRunner(
            mdns=mdns_text(('adb-bfd7e543-MoDo9u', '_adb-tls-connect._tcp.',
                            '192.168.1.5:41001')),
            connect_map={
                '192.168.1.5:41001': (0, 'connected to 192.168.1.5:41001')})
        ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847',
                                               '123456', r)
        self.assertTrue(ok)
        self.assertEqual(serial, '192.168.1.5:41001')
        self.assertEqual(r.cmds('connect'), [['connect', '192.168.1.5:41001']])
        self.assertEqual(r.cmds('pair'),
                         [['pair', '192.168.1.5:43847', '123456']])

    def test_pair_ok_all_fail_sleep_patched(self):
        """配对成功但一直等不到连接广播 → 中文提示（含手动填端口的办法）。"""
        r = FakeRunner(mdns='List of discovered mdns services\n')
        logs = []
        with mock.patch('connect_ui.time.sleep') as sl:
            ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847',
                                                   '123456', r, logs.append)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assert_plain_chinese(msg)
        self.assertIn('配对成功', msg)
        self.assertIn('端口', msg)
        self.assertIn('无线调试', msg)
        self.assertEqual(sl.call_count, cu.PAIR_DISCOVER_SECS)   # 按常量等这么多秒
        self.assertEqual(len(r.cmds('devices')), cu.PAIR_DISCOVER_SECS)
        # 放弃时把原始 mdns 输出写进日志，便于定位「广播到底有没有来」
        self.assertTrue(any('mdns=' in m for m in logs))

    def test_pair_ok_port_found_but_connect_failed(self):
        """端口找到了但 connect 失败 → 报真实的连接错误，不能说「没找到端口」。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-bfd7e543-MoDo9u', '_adb-tls-connect._tcp.',
                            '192.168.1.5:41001')),
            connect_default=(1, 'failed to connect to 192.168.1.5:41001: 10060'))
        with mock.patch('connect_ui.time.sleep'):
            ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847',
                                                   '123456', r)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assertIn('端口也找到了', msg)
        self.assertIn('192.168.1.5:41001', msg)
        self.assertNotIn('没收到', msg)
        self.assert_plain_chinese(msg, '10060')

    def test_invalid_input_short_circuits(self):
        """IP:端口 / 配对码不合法 → 中文提示，且一个 adb 命令都不跑。"""
        r = FakeRunner()
        ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5', '123456', r)
        self.assertFalse(ok)
        self.assert_plain_chinese(msg)
        ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847', '123', r)
        self.assertFalse(ok)
        self.assert_plain_chinese(msg)
        self.assertEqual(r.calls, [])

    def test_mdns_wrong_ip_not_used(self):
        """mDNS 里只有别的 IP 的服务 → 不能拿来连，最终走失败提示。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-other', '_adb-tls-connect._tcp.',
                            '10.0.0.9:41001')))
        with mock.patch('connect_ui.time.sleep'):
            ok, msg, serial = cu.pair_then_connect(ADB, '192.168.1.5:43847',
                                                   '123456', r)
        self.assertFalse(ok)
        self.assertEqual(r.cmds('connect'), [])


class DecideActionTest(ChineseMsgMixin, unittest.TestCase):
    """「连接手机」按钮的分派：有配对码→配对+连接；配对码留空→直接连接。"""

    def test_with_code_pairs(self):
        kind, payload, err = cu.decide_action('192.168.1.5', '43847', '123456')
        self.assertEqual(kind, 'pair')
        self.assertEqual(payload, ('192.168.1.5:43847', '123456'))
        self.assertEqual(err, '')

    def test_empty_code_connects_directly(self):
        """已配对过：配对码留空，填好 IP 和连接端口就直接连（不重新配对）。"""
        kind, payload, err = cu.decide_action('192.168.1.5', '41001', '')
        self.assertEqual(kind, 'connect')
        self.assertEqual(payload, '192.168.1.5:41001')
        self.assertEqual(err, '')

    def test_short_code_is_error(self):
        kind, payload, err = cu.decide_action('192.168.1.5', '43847', '123')
        self.assertEqual(kind, '')
        self.assertIsNone(payload)
        self.assert_plain_chinese(err)
        self.assertIn('留空', err)

    def test_only_code_pairs_by_discovery(self):
        """只填配对码、IP 端口留空：交给电脑自己去手机广播里找端口。"""
        kind, payload, err = cu.decide_action('', '', '123456')
        self.assertEqual(kind, 'pair')
        self.assertEqual(payload, ('', '123456'))
        self.assertEqual(err, '')

    def test_missing_everything_is_error(self):
        kind, payload, err = cu.decide_action('', '', '')
        self.assertEqual(kind, '')
        self.assertIsNone(payload)
        self.assert_plain_chinese(err)
        self.assertIn('配对码', err)


class DiscoverPairTargetTest(unittest.TestCase):
    """自动找手机的配对端口：连接广播给 IP，配对广播给端口。"""

    def browser(self, srv_connect=(), srv_pairing=()):
        calls = []

        def browse(service_type, timeout=None, targets=None):
            calls.append((service_type, list(targets or [])))
            return list(srv_connect if 'connect' in service_type else srv_pairing)

        browse.calls = calls
        return browse

    def test_uses_ip_from_connect_broadcast(self):
        browse = self.browser(
            srv_connect=[{'name': 'x', 'port': 36305, 'addr': '192.168.1.5'}],
            srv_pairing=[{'name': 'y', 'port': 43485, 'addr': '192.168.1.5'}])
        got = cu.discover_pair_target(browser=browse)
        self.assertEqual(got, '192.168.1.5:43485')
        self.assertEqual(browse.calls[0][0], '_adb-tls-connect._tcp')
        self.assertEqual(browse.calls[1][0], '_adb-tls-pairing._tcp')
        # 配对广播必须单播问手机（组播问不到）
        self.assertIn('192.168.1.5', browse.calls[1][1])

    def test_peers_from_known_devices_are_asked(self):
        browse = self.browser(srv_pairing=[{'addr': '192.168.1.9', 'port': 40000}])
        got = cu.discover_pair_target(['192.168.1.9', ''], browser=browse)
        self.assertEqual(got, '192.168.1.9:40000')
        # 连接广播每轮都要问（历史地址可能过期），单播目标里要有已知 IP
        self.assertEqual(browse.calls[0][0], '_adb-tls-connect._tcp')
        self.assertEqual(browse.calls[1][0], '_adb-tls-pairing._tcp')
        self.assertIn('192.168.1.9', browse.calls[1][1])

    def test_invalid_peer_addresses_skipped(self):
        """'...'、'1.2.3.4.5' 这类不是 IP，不能拿去当单播目标。"""
        browse = self.browser(srv_pairing=[{'addr': '192.168.1.9', 'port': 40000}])
        got = cu.discover_pair_target(['...', '1.2.3.4.5', '999.1.1.1'], browser=browse)
        self.assertEqual(got, '192.168.1.9:40000')
        self.assertEqual(browse.calls[1][1], [])

    def test_nothing_found_returns_empty(self):
        self.assertEqual(cu.discover_pair_target(browser=self.browser()), '')

    def test_ip_only_accepts_both_forms(self):
        self.assertEqual(cu._ip_only('192.168.1.5'), '192.168.1.5')
        self.assertEqual(cu._ip_only('192.168.1.5:43847'), '192.168.1.5')
        self.assertEqual(cu._ip_only('  10.0.0.7 '), '10.0.0.7')

    def test_ip_only_rejects_garbage(self):
        for bad in ('', '...', '1.2.3.4.5', '999.1.1.1', 'hello', '192.168.1'):
            self.assertEqual(cu._ip_only(bad), '', '不该把 %r 当 IP' % bad)

    def test_item_without_port_ignored(self):
        browse = self.browser(srv_pairing=[{'addr': '192.168.1.5', 'port': 0}])
        self.assertEqual(cu.discover_pair_target(browser=browse), '')


class ConnectOnlyTest(ChineseMsgMixin, unittest.TestCase):
    def test_connected_to_success(self):
        """'connected to ...' → 成功，返回目标串号。"""
        r = FakeRunner(connect_map={
            '192.168.1.5:41001': (0, 'connected to 192.168.1.5:41001')})
        ok, msg, serial = cu.connect_only(ADB, '192.168.1.5:41001', r)
        self.assertTrue(ok)
        self.assertEqual(msg, '连接成功')
        self.assertEqual(serial, '192.168.1.5:41001')

    def test_already_connected_success(self):
        """'already connected' 也算成功。"""
        r = FakeRunner(connect_map={
            '192.168.1.5:41001': (0, 'already connected to 192.168.1.5:41001')})
        ok, msg, serial = cu.connect_only(ADB, '192.168.1.5:41001', r)
        self.assertTrue(ok)
        self.assertEqual(serial, '192.168.1.5:41001')

    def test_failed_10061_chinese(self):
        """'failed to connect ... 10061' → 失败，提示为中文端口指引。"""
        r = FakeRunner(connect_map={
            '192.168.1.5:41001': (
                1, 'failed to connect to 192.168.1.5:41001: '
                   'Connection refused (10061)')})
        ok, msg, serial = cu.connect_only(ADB, '192.168.1.5:41001', r)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assertIn('端口不对', msg)
        self.assert_plain_chinese(msg, '10061', 'failed to connect',
                                  'connection refused')

    def test_invalid_ip_port(self):
        """输入不合法 → 中文填写指引，不跑 adb。"""
        r = FakeRunner()
        ok, msg, serial = cu.connect_only(ADB, '192.168.1.5', r)
        self.assertFalse(ok)
        self.assertEqual(serial, '')
        self.assert_plain_chinese(msg)
        self.assertEqual(r.calls, [])

    def test_stale_port_retries_with_fresh_mdns_port(self):
        """「新发现」行的端口往往已过期：connect 失败后自动用 mDNS 最新端口重试。"""
        r = FakeRunner(
            mdns=('List of discovered mdns services\n'
                  'adb-bfd7e543-MoDo9u\t_adb-tls-connect._tcp\t'
                  '192.168.137.144:45197\n'),
            connect_map={'192.168.137.144:45197': (0, 'connected to 192.168.137.144:45197')})
        ok, msg, serial = cu.connect_only(ADB, '192.168.137.144:36183', r)
        self.assertTrue(ok, '过期端口应能自动用新端口连上：%s' % msg)
        self.assertEqual(serial, '192.168.137.144:45197')

    def test_resolve_serial_uses_what_adb_reports(self):
        """连接成功后串号口径统一：adb 报 mDNS 串号就用它（否则 scrcpy 启动即退出）。"""
        r = FakeRunner(devices=('List of devices attached\n'
                                '%s\tdevice product:PJZ110 model:PJZ110\n' % MDNS_SERIAL),
                       mdns=('adb-bfd7e543-MoDo9u\t_adb-tls-connect._tcp\t'
                             '192.168.137.144:45197\n'))
        self.assertEqual(cu.resolve_serial(ADB, '192.168.137.144:45197', r), MDNS_SERIAL)


class DeviceDisplayNameTest(unittest.TestCase):
    def test_first_nonempty_prop_wins(self):
        """getprop 输出第一行非空营销名优先（如 '一加 13'）。"""
        r = FakeRunner(shell_out='一加 13\n\n\nPJZ110\n')
        name = cu.device_display_name(ADB, MDNS_SERIAL, 'PJZ110', r)
        self.assertEqual(name, '一加 13')
        self.assertEqual(r.calls[0][1], 6)   # timeout 透传给 runner

    def test_mdns_like_prop_values_skipped(self):
        """prop 值若是机器串号（adb- 开头 / 含 _tcp）必须跳过。"""
        r = FakeRunner(shell_out='adb-bfd7e543-MoDo9u\nfoo_tcp\n一加 13\n')
        name = cu.device_display_name(ADB, MDNS_SERIAL, '', r)
        self.assertEqual(name, '一加 13')

    def test_all_empty_falls_back_to_model(self):
        """getprop 全空 → 退回 model 参数。"""
        r = FakeRunner(shell_out='\n\n  \n')
        name = cu.device_display_name(ADB, MDNS_SERIAL, 'PJZ110', r)
        self.assertEqual(name, 'PJZ110')

    def test_runner_exception_falls_back_to_model(self):
        """runner 抛异常（超时等）→ 不炸，退回 model。"""
        def boom(cmd, timeout=25):
            raise RuntimeError('adb timeout')
        name = cu.device_display_name(ADB, MDNS_SERIAL, 'PJZ110', boom)
        self.assertEqual(name, 'PJZ110')

    def test_ip_serial_fallback(self):
        """无营销名无型号、串号是 ip:port → '无线设备 <ip>'。"""
        r = FakeRunner(shell_out='')
        name = cu.device_display_name(ADB, '192.168.1.5:41001', '', r)
        self.assertEqual(name, '无线设备 192.168.1.5')

    def test_mdns_serial_never_shows_raw_segment(self):
        """mDNS 串号中间那截（MoDo9u）用户看不懂，绝不能当名字显示。"""
        r = FakeRunner(shell_out='')
        name = cu.device_display_name(ADB, MDNS_SERIAL, '', r)
        self.assertEqual(name, '无线设备')
        self.assertNotIn('MoDo9u', name)
        self.assertNotIn('_tcp', name)

    def test_mdns_serial_without_middle_never_raw(self):
        """mDNS 串号没有中间段时也不能原样显示（应兜底）。"""
        r = FakeRunner(shell_out='')
        serial = 'adb-bfd7e543._adb-tls-connect._tcp'
        name = cu.device_display_name(ADB, serial, '', r)
        self.assertNotEqual(name, serial, 'mDNS 串号被直接当名字显示了')
        self.assertNotIn('_tcp', name)

    def test_empty_serial_unknown(self):
        """串号为空且无型号 → '未知设备'。"""
        r = FakeRunner()
        self.assertEqual(cu.device_display_name(ADB, '', '', r), '未知设备')
        self.assertEqual(r.calls, [])


class RememberDeviceTest(unittest.TestCase):
    def test_new_device_inserted_first(self):
        """新设备插最前，last_seen 用注入的 now。"""
        cfg = {'known_devices': [
            {'serial': 'OLD1', 'name': '旧手机', 'addr': '', 'last_seen': 1}]}
        out = cu.remember_device(cfg, 'NEW1', '新手机', '', now=100)
        self.assertIs(out, cfg['known_devices'])
        self.assertEqual(cfg['known_devices'][0]['serial'], 'NEW1')
        self.assertEqual(cfg['known_devices'][0]['name'], '新手机')
        self.assertEqual(cfg['known_devices'][0]['last_seen'], 100)
        self.assertEqual(len(cfg['known_devices']), 2)

    def test_same_serial_dedup_update(self):
        """同 serial 去重更新：不重复、名字/时间刷新。"""
        cfg = {'known_devices': [
            {'serial': 'S1', 'name': '旧名', 'addr': '', 'last_seen': 1},
            {'serial': 'S2', 'name': '别的', 'addr': '', 'last_seen': 1}]}
        cu.remember_device(cfg, 'S1', '新名', '', now=200)
        known = cfg['known_devices']
        self.assertEqual(len(known), 2)
        self.assertEqual(known[0]['serial'], 'S1')
        self.assertEqual(known[0]['name'], '新名')
        self.assertEqual(known[0]['last_seen'], 200)

    def test_same_ip_new_port_invalidates_old(self):
        """同 IP 换端口：旧记录作废，只留新端口记录。"""
        cfg = {'known_devices': [
            {'serial': '192.168.1.5:5555', 'name': '一加 13',
             'addr': '192.168.1.5:5555', 'last_seen': 1},
            {'serial': 'USB1', 'name': '别的手机', 'addr': '', 'last_seen': 1}]}
        cu.remember_device(cfg, '192.168.1.5:41001', '一加 13', '', now=2)
        serials = [k['serial'] for k in cfg['known_devices']]
        self.assertEqual(serials, ['192.168.1.5:41001', 'USB1'])

    def test_ipport_folds_into_mdns_entry(self):
        """已记过 mDNS 串号时，记住 ip:port 不该再新增一条历史（同一台手机）。"""
        cfg = {'known_devices': [{'serial': MDNS_SERIAL, 'name': '',
                                  'addr': '', 'last_seen': 1}]}
        cu.remember_device(cfg, '192.168.137.144:45197', '', '', now=5)
        known = cfg['known_devices']
        self.assertEqual(len(known), 1, '不该多记一条：%r' % known)
        self.assertEqual(known[0]['serial'], MDNS_SERIAL)
        self.assertEqual(known[0]['addr'], '192.168.137.144:45197')
        self.assertEqual(known[0]['last_seen'], 5)
        self.assertNotIn('_tcp', known[0]['name'])

    def test_serial_never_stored_as_name(self):
        """名字传空/等于串号时，存「无线设备 IP」这类可读名字，不存串号。"""
        cfg = {}
        cu.remember_device(cfg, '192.168.137.144:45197', '', now=1)
        self.assertEqual(cfg['known_devices'][0]['name'], '无线设备 192.168.137.144')

    def test_remembered_name_ignores_junk_names(self):
        """旧配置把串号当名字存过：显示时也要过滤掉，不能顶到界面上。"""
        known = [{'serial': MDNS_SERIAL, 'name': MDNS_SERIAL, 'addr': ''}]
        row = {'serial': MDNS_SERIAL, 'addr': '', 'name': ''}
        self.assertEqual(cu._remembered_name(row, known), '无线设备')
        known2 = [{'serial': '192.168.137.144:45197',
                   'name': '192.168.137.144:45197',
                   'addr': '192.168.137.144:45197'}]
        row2 = {'serial': '192.168.137.144:45197',
                'addr': '192.168.137.144:45197', 'name': ''}
        self.assertEqual(cu._remembered_name(row2, known2), '无线设备 192.168.137.144')

    def test_known_mdns_replace_drops_old_ipport(self):
        """先记 ip:port，再记同一台的 mDNS 串号：旧 ip:port 记录作废。"""
        cfg = {'known_devices': [
            {'serial': '192.168.137.144:45197', 'name': '无线设备 192.168.137.144',
             'addr': '192.168.137.144:45197', 'last_seen': 1}]}
        cu.remember_device(cfg, MDNS_SERIAL, '一加 13', '', now=2)
        self.assertEqual([k['serial'] for k in cfg['known_devices']], [MDNS_SERIAL])

    def test_cap_at_eight(self):
        """最多 8 台：第 9 台进来时最旧的被挤掉。"""
        cfg = {'known_devices': [
            {'serial': 'U%d' % i, 'name': 'n%d' % i, 'addr': '', 'last_seen': i}
            for i in range(8)]}
        cu.remember_device(cfg, 'NEW', '新', '', now=99)
        known = cfg['known_devices']
        self.assertEqual(len(known), 8)
        self.assertEqual(known[0]['serial'], 'NEW')
        self.assertNotIn('U7', [k['serial'] for k in known])

    def test_creates_missing_key(self):
        """cfg 无 known_devices 键 → 自动创建。"""
        cfg = {}
        out = cu.remember_device(cfg, 'S1', '手机', '', now=5)
        self.assertIn('known_devices', cfg)
        self.assertEqual(len(out), 1)
        self.assertEqual(cfg['known_devices'][0]['serial'], 'S1')

    def test_non_list_known_rebuilt(self):
        """known_devices 不是列表（脏配置）→ 重建为列表。"""
        cfg = {'known_devices': None}
        cu.remember_device(cfg, 'S1', '手机', '', now=5)
        self.assertIsInstance(cfg['known_devices'], list)
        self.assertEqual(len(cfg['known_devices']), 1)

    def test_addr_defaults_to_ipport_serial(self):
        """addr 缺省时，ip:port 串号自身就是 addr。"""
        cfg = {}
        cu.remember_device(cfg, '192.168.1.5:41001', '手机', '', now=5)
        self.assertEqual(cfg['known_devices'][0]['addr'], '192.168.1.5:41001')

    def test_name_defaults_to_serial_and_now_defaults(self):
        """name 为空退回串号；now 缺省用当前时间（打桩验证）。"""
        cfg = {}
        with mock.patch('connect_ui.time.time', return_value=12345.9):
            cu.remember_device(cfg, 'S1', '', '')
        entry = cfg['known_devices'][0]
        self.assertEqual(entry['name'], 'S1')
        self.assertEqual(entry['last_seen'], 12345)


class TryAutoConnectTest(unittest.TestCase):
    def test_online_device_returned_directly(self):
        """已有在线设备 → 直接返回第一台，不做 connect/mdns。"""
        r = FakeRunner(devices=devices_text('ABC123', '192.168.1.5:5555'))
        self.assertEqual(cu.try_auto_connect(ADB, '', r), 'ABC123')
        self.assertEqual(r.cmds('connect'), [])
        self.assertEqual(r.cmds('mdns'), [])

    def test_cfg_ipport_connect_success(self):
        """无在线设备 → 按 cfg 的 ip:port connect 成功。"""
        r = FakeRunner(connect_map={
            '192.168.1.5:5555': (0, 'connected to 192.168.1.5:5555')})
        self.assertEqual(cu.try_auto_connect(ADB, '192.168.1.5:5555', r),
                         '192.168.1.5:5555')
        self.assertEqual(r.cmds('connect'), [['connect', '192.168.1.5:5555']])

    def test_mdns_fallback_same_ip_new_port(self):
        """cfg 端口失败 → mDNS 找到同 IP 新端口 → connect 成功。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-bfd7e543-MoDo9u', '_adb-tls-connect._tcp.',
                            '192.168.1.5:41001')),
            connect_map={
                '192.168.1.5:5555': (1, 'failed to connect: 10061'),
                '192.168.1.5:41001': (0, 'connected to 192.168.1.5:41001')})
        self.assertEqual(cu.try_auto_connect(ADB, '192.168.1.5:5555', r),
                         '192.168.1.5:41001')
        self.assertEqual(r.cmds('connect'),
                         [['connect', '192.168.1.5:5555'],
                          ['connect', '192.168.1.5:41001']])

    def test_mdns_fallback_any_ip_when_no_cfg(self):
        """cfg 为空 → mDNS 任意一台 tls-connect 服务兜底。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-009b17d0', '_adb-tls-connect._tcp.',
                            '20.30.1.217:5555')),
            connect_map={
                '20.30.1.217:5555': (0, 'connected to 20.30.1.217:5555')})
        self.assertEqual(cu.try_auto_connect(ADB, '', r), '20.30.1.217:5555')

    def test_all_fail_returns_empty(self):
        """在线/配置端口/mDNS 全失败 → 返回空串（此时才弹向导）。"""
        r = FakeRunner(mdns='List of discovered mdns services\n')
        self.assertEqual(cu.try_auto_connect(ADB, '192.168.1.5:5555', r), '')

    def test_mdns_same_as_cfg_not_connected_twice(self):
        """mDNS 找到的就是 cfg 端口 → 只 connect 一次。"""
        r = FakeRunner(
            mdns=mdns_text(('adb-x', '_adb-tls-connect._tcp.',
                            '192.168.1.5:5555')),
            connect_map={
                '192.168.1.5:5555': (0, 'connected to 192.168.1.5:5555')})
        self.assertEqual(cu.try_auto_connect(ADB, '192.168.1.5:5555', r),
                         '192.168.1.5:5555')
        self.assertEqual(len(r.cmds('connect')), 1)

    def test_non_ipport_cfg_skips_direct_connect(self):
        """cfg 串号不是 ip:port（如 USB 串号）→ 不盲目 connect，走 mDNS。"""
        r = FakeRunner(mdns='List of discovered mdns services\n')
        self.assertEqual(cu.try_auto_connect(ADB, 'ABC123', r), '')
        self.assertEqual(r.cmds('connect'), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)