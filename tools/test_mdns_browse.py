# -*- coding: utf-8 -*-
"""mdns_browse 的报文解析单测：全部用自己拼的 DNS 报文，不碰网络。

为什么要测：这个模块是用来「听手机的配对广播」的，手机与电脑之间只有一次机会；
解析错了只会表现为「什么都没发现」，很难从现象上定位。所以把 DNS 解析的每个
环节都用固定报文钉住（域名编码、压缩指针、PTR/SRV/A 合并）。
"""
import socket
import struct
import time
import unittest

import mdns_browse as mb


def _name(text):
    return mb.encode_name(text)


def _rec(label_bytes, rtype, rdata, cls=mb.CLASS_IN, ttl=120):
    return label_bytes + struct.pack('>HHIH', rtype, cls, ttl, len(rdata)) + rdata


def _packet(records, qd=0, flags=0x8400):
    head = struct.pack('>HHHHHH', 0, flags, qd, len(records), 0, 0)
    return head + b''.join(records)


def _sample_packet(port=41234, addr='192.168.137.144'):
    """PTR + SRV + A 的完整响应（与真机广播结构一致）。"""
    inst = 'adb-abcdef-ABCDEF._adb-tls-pairing._tcp.local'
    host = 'Android_7P765W30.local'
    ptr = _rec(_name('_adb-tls-pairing._tcp.local'), mb.TYPE_PTR, _name(inst))
    srv = _rec(_name(inst), mb.TYPE_SRV,
               struct.pack('>HHH', 0, 0, port) + _name(host))
    a = _rec(_name(host), mb.TYPE_A, socket.inet_aton(addr))
    return inst, host, _packet([ptr, srv, a])


class NameCodecTest(unittest.TestCase):
    def test_full_name_appends_local(self):
        self.assertEqual(mb._full_name('_adb-tls-pairing._tcp'),
                         '_adb-tls-pairing._tcp.local')
        self.assertEqual(mb._full_name('_adb-tls-pairing._tcp.local'),
                         '_adb-tls-pairing._tcp.local')
        self.assertEqual(mb._full_name('  _adb._tcp.  '), '_adb._tcp.local')

    def test_full_name_rejects_empty(self):
        with self.assertRaises(ValueError):
            mb._full_name('   ')

    def test_encode_decode_roundtrip(self):
        name = '_adb-tls-pairing._tcp.local'
        raw = _name(name)
        self.assertEqual(raw[0], len('_adb-tls-pairing'))
        got, off = mb.read_name(raw, 0)
        self.assertEqual(got, name)
        self.assertEqual(off, len(raw))

    def test_encode_rejects_long_label(self):
        with self.assertRaises(ValueError):
            _name('a' * 64 + '.local')

    def test_read_name_follows_compression_pointer(self):
        # 前 12 字节是头，名字放在 12，指针指向 12
        target = _name('Android_7P765W30.local')
        ptr = bytes([0xC0, 12])
        data = b'\x00' * 12 + target + ptr
        got, off = mb.read_name(data, 12 + len(target))
        self.assertEqual(got, 'Android_7P765W30.local')
        self.assertEqual(off, 12 + len(target) + 2)

    def test_read_name_detects_pointer_loop(self):
        data = b'\x00' * 12 + bytes([0xC0, 12])
        with self.assertRaises(ValueError):
            mb.read_name(data, 12)

    def test_read_name_truncated(self):
        with self.assertRaises(ValueError):
            mb.read_name(b'\x05ab', 0)


class BuildQueryTest(unittest.TestCase):
    def test_query_shape(self):
        pkt = mb.build_query('_adb-tls-pairing._tcp')
        self.assertEqual(len(pkt) >= 12, True)
        _tid, _flags, qd, an, ns, ar = struct.unpack('>HHHHHH', pkt[:12])
        self.assertEqual((qd, an, ns, ar), (1, 0, 0, 0))
        name, off = mb.read_name(pkt, 12)
        self.assertEqual(name, '_adb-tls-pairing._tcp.local')
        rtype, rclass = struct.unpack('>HH', pkt[off:off + 4])
        self.assertEqual(rtype, mb.TYPE_PTR)
        self.assertEqual(rclass, mb.CLASS_IN | mb.CLASS_QU)

    def test_query_without_unicast_bit(self):
        pkt = mb.build_query('_adb._tcp', unicast=False)
        name, off = mb.read_name(pkt, 12)
        self.assertEqual(name, '_adb._tcp.local')
        _rtype, rclass = struct.unpack('>HH', pkt[off:off + 4])
        self.assertEqual(rclass, mb.CLASS_IN)


class ParseMessageTest(unittest.TestCase):
    def test_parses_full_answer(self):
        inst, host, pkt = _sample_packet()
        records = mb.parse_message(pkt)
        types = [r[1] for r in records]
        self.assertEqual(types, [mb.TYPE_PTR, mb.TYPE_SRV, mb.TYPE_A])

    def test_skips_question_section(self):
        q = _name('_adb-tls-pairing._tcp.local') + struct.pack('>HH', mb.TYPE_PTR, mb.CLASS_IN)
        inst = 'adb-abcdef-ABCDEF._adb-tls-pairing._tcp.local'
        rec = _rec(_name(inst), mb.TYPE_A, socket.inet_aton('10.0.0.5'))
        pkt = struct.pack('>HHHHHH', 0, 0x8400, 1, 1, 0, 0) + q + rec
        records = mb.parse_message(pkt)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0], inst)

    def test_truncated_answer_raises(self):
        q = _name('_adb-tls-pairing._tcp.local') + struct.pack('>HH', mb.TYPE_PTR, mb.CLASS_IN)
        pkt = struct.pack('>HHHHHH', 0, 0x8400, 1, 1, 0, 0) + q   # 声称有 1 条答案但没内容
        with self.assertRaises(ValueError):
            mb.parse_message(pkt)

    def test_rejects_short_packet(self):
        with self.assertRaises(ValueError):
            mb.parse_message(b'\x00\x01')


class CollectTest(unittest.TestCase):
    def test_merges_ptr_srv_a(self):
        inst, host, pkt = _sample_packet(port=41234, addr='192.168.137.144')
        out = mb.collect(mb.parse_message(pkt), pkt)
        self.assertEqual(len(out), 1)
        item = out[0]
        self.assertEqual(item['name'], inst)
        self.assertEqual(item['host'], host)
        self.assertEqual(item['port'], 41234)
        self.assertEqual(item['addr'], '192.168.137.144')

    def test_srv_target_as_compression_pointer(self):
        host = 'Android_7P765W30.local'
        inst = 'adb-abcdef-ABCDEF._adb-tls-pairing._tcp.local'
        a = _rec(_name(host), mb.TYPE_A, socket.inet_aton('192.168.137.144'))
        host_off = 12 + len(a) - len(socket.inet_aton('192.168.137.144')) \
            - 10 - len(_name(host))
        srv = _rec(_name(inst), mb.TYPE_SRV,
                   struct.pack('>HHH', 0, 0, 41234) + bytes([0xC0, host_off]))
        pkt = _packet([a, srv])
        out = mb.collect(mb.parse_message(pkt), pkt)
        self.assertEqual(out[0]['port'], 41234)
        self.assertEqual(out[0]['addr'], '192.168.137.144')

    def test_ignores_broken_record(self):
        inst = 'x._adb-tls-pairing._tcp.local'
        bad = _rec(_name(inst), mb.TYPE_SRV, b'\x00\x00')          # rdlen<7
        good = _rec(_name(inst), mb.TYPE_A, socket.inet_aton('10.0.0.9'))
        pkt = _packet([bad, good])
        out = mb.collect(mb.parse_message(pkt), pkt)
        self.assertEqual(out, [])


class FakeSock(object):
    def __init__(self, packets):
        self.packets = list(packets)
        self.sent = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def settimeout(self, value):
        pass

    def recvfrom(self, size):
        if self.packets:
            return self.packets.pop(0), ('192.168.137.144', mb.MDNS_PORT)
        raise socket.timeout()

    def close(self):
        self.closed = True


class BrowseTest(unittest.TestCase):
    def test_browse_returns_service(self):
        inst, host, pkt = _sample_packet(port=41234, addr='192.168.137.144')
        sock = FakeSock([pkt])
        got = mb.browse('_adb-tls-pairing._tcp', timeout=0.2, sock=sock)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['name'], inst)
        self.assertEqual(got[0]['port'], 41234)
        self.assertEqual(got[0]['addr'], '192.168.137.144')
        self.assertEqual(sock.sent[0][1], (mb.MDNS_ADDR, mb.MDNS_PORT))
        self.assertFalse(sock.closed)          # 外部传进来的 socket 不归它关

    def test_browse_keeps_addr_from_earlier_packet(self):
        inst = 'adb-abcdef-ABCDEF._adb-tls-pairing._tcp.local'
        host = 'Android_7P765W30.local'
        full = _packet([
            _rec(_name(inst), mb.TYPE_SRV, struct.pack('>HHH', 0, 0, 41234) + _name(host)),
            _rec(_name(host), mb.TYPE_A, socket.inet_aton('192.168.137.144')),
        ])
        only_srv = _packet([
            _rec(_name(inst), mb.TYPE_SRV, struct.pack('>HHH', 0, 0, 41234) + _name(host)),
        ])
        sock = FakeSock([full, only_srv])
        got = mb.browse('_adb-tls-pairing._tcp', timeout=0.3, sock=sock)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['addr'], '192.168.137.144')   # 后来那包没 A 记录，别把 ip 清空

    def test_browse_ignores_garbage(self):
        sock = FakeSock([b'\x01\x02\x03', b'', b'\x00' * 40])
        got = mb.browse('_adb-tls-pairing._tcp', timeout=0.2, sock=sock)
        self.assertEqual(got, [])

    def test_browse_reports_nothing_when_silent(self):
        got = mb.browse('_adb-tls-pairing._tcp', timeout=0.2, sock=FakeSock([]))
        self.assertEqual(got, [])

    def test_browse_also_asks_targets_by_unicast(self):
        sock = FakeSock([])
        mb.browse('_adb-tls-pairing._tcp', timeout=0.2, sock=sock,
                  targets=['192.168.137.144', '', None, '10.0.0.7'])
        dests = [addr for _data, addr in sock.sent]
        self.assertIn((mb.MDNS_ADDR, mb.MDNS_PORT), dests)
        self.assertIn(('192.168.137.144', mb.MDNS_PORT), dests)
        self.assertIn(('10.0.0.7', mb.MDNS_PORT), dests)
        self.assertNotIn(('', mb.MDNS_PORT), dests)
        # 同一轮里每个 target 只问一次
        self.assertEqual(len(dests), 1 + 2)

    def test_addr_falls_back_to_packet_source(self):
        """SRV 包不带 A 记录时用回包源地址当 IP（安卓常把 SRV 和 A 分两个包发）。"""
        inst = 'adb-abcdef-ABCDEF._adb-tls-pairing._tcp.local'
        host = 'Android_7P765W30.local'
        pkt = _packet([_rec(_name(inst), mb.TYPE_SRV,
                            struct.pack('>HHH', 0, 0, 41234) + _name(host))])
        got = mb.browse('_adb-tls-pairing._tcp', timeout=0.2, sock=FakeSock([pkt]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['port'], 41234)
        self.assertEqual(got[0]['addr'], '192.168.137.144',
                         '没有用回包源地址兜底，发现会因「有端口没 IP」失败')

    def test_bad_target_does_not_block_the_rest(self):
        """一个非法单播目标发不出去，不能连坐它后面的目标。"""
        class PickySock(FakeSock):
            def sendto(self, data, addr):
                if addr[0] == 'bad':
                    raise OSError('invalid argument')
                super(PickySock, self).sendto(data, addr)

        sock = PickySock([])
        mb.browse('_adb-tls-pairing._tcp', timeout=0.2, sock=sock,
                  targets=['bad', '10.0.0.7'])
        self.assertIn(('10.0.0.7', mb.MDNS_PORT), [a for _d, a in sock.sent])


if __name__ == '__main__':
    unittest.main()
