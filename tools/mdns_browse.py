# -*- coding: utf-8 -*-
"""极简 mDNS 浏览：问一句「谁的 <服务类型> 在哪」，把答案整理成列表。

为什么要自己写：
  * `adb mdns services` 只浏览 `_adb-tls-connect._tcp`（连接服务）。扫码配对时
    手机广播的是 `_adb-tls-pairing._tcp`，adb 根本不会列出来 —— 拿不到端口就
    没法 `adb pair`。
  * 魔芋是纯标准库 + ctypes 的风格，为一次 PTR 查询引进 zeroconf 这种大依赖不划算，
    这里用 socket 拼一个 DNS 报文就够（RFC 6762）。

对外只有两个函数：
  browse('_adb-tls-pairing._tcp', 5)      # → [{'name','host','port','addr'}]
  parse_message(bytes)                    # 解析响应报文，测试用
"""
import socket
import struct
import time

MDNS_ADDR = '224.0.0.251'
MDNS_PORT = 5353

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_AAAA = 28
TYPE_SRV = 33

CLASS_IN = 1
CLASS_QU = 0x8000          # 请求单播响应（RFC 6762 §5.4）


def _full_name(service_type):
    """'_adb-tls-pairing._tcp' → '_adb-tls-pairing._tcp.local'（已带 .local 就原样）。"""
    st = (service_type or '').strip().strip('.')
    if not st:
        raise ValueError('service_type 不能为空')
    if not st.lower().endswith('.local'):
        st += '.local'
    return st


def encode_name(name):
    """'a.b.local' → b'\\x01a\\x01b\\x05local\\x00'"""
    out = bytearray()
    for label in (name or '').strip('.').split('.'):
        raw = label.encode('utf-8')
        if not raw:
            continue
        if len(raw) > 63:
            raise ValueError('DNS 标签超过 63 字节：%r' % label)
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def read_name(data, off):
    """从 off 处读一个（可能带压缩指针的）域名 → (名字, 名字之后的下一个偏移)。"""
    labels = []
    end = None
    jumps = 0
    while True:
        if off >= len(data):
            raise ValueError('报文被截断')
        n = data[off]
        if n == 0:
            off += 1
            if end is None:
                end = off
            break
        if n & 0xC0 == 0xC0:                    # 压缩指针
            if off + 1 >= len(data):
                raise ValueError('压缩指针被截断')
            ptr = ((n & 0x3F) << 8) | data[off + 1]
            if end is None:
                end = off + 2
            off = ptr
            jumps += 1
            if jumps > 32:
                raise ValueError('压缩指针成环')
            continue
        labels.append(data[off + 1:off + 1 + n].decode('utf-8', 'replace'))
        off += 1 + n
        if off > len(data):
            raise ValueError('报文被截断')
    return '.'.join(labels), (off if end is None else end)


def build_query(service_type, unicast=True):
    """拼一个 PTR 查询报文。"""
    name = _full_name(service_type)
    if not name.lower().endswith('.local'):
        name += '.local'
    pkt = bytearray(struct.pack('>HHHHHH', 0, 0, 1, 0, 0, 0))
    pkt += encode_name(name)
    pkt += struct.pack('>HH', TYPE_PTR, CLASS_IN | (CLASS_QU if unicast else 0))
    return bytes(pkt)


def parse_message(data):
    """DNS 报文 → [(记录名, 类型, rdata_偏移, rdata 长度)]。"""
    if len(data) < 12:
        raise ValueError('报文太短')
    _tid, _flags, qd, an, ns, ar = struct.unpack('>HHHHHH', data[:12])
    off = 12
    for _ in range(qd):
        _name, off = read_name(data, off)
        off += 4                                  # type + class
    records = []
    for _ in range(an + ns + ar):
        name, off = read_name(data, off)
        if off + 10 > len(data):
            raise ValueError('记录头被截断')
        rtype, _rclass, _ttl, rdlen = struct.unpack('>HHIH', data[off:off + 10])
        off += 10
        if off + rdlen > len(data):
            raise ValueError('rdata 被截断')
        records.append((name, rtype, off, rdlen))
        off += rdlen
    return records


def _rd_name(data, off, rdlen):
    name, _ = read_name(data, off)
    return name


def collect(records, data):
    """把记录表整理成服务列表：[{'name','host','port','addr'}]。"""
    host_addr = {}
    srv = {}             # 实例名 → (host, port)
    names = []
    seen_lower = set()

    def add(name):
        # 大小写不敏感去重（key 用的是 lower，别让同名不同大小写产出两条、其中一条没端口）
        if name and name.lower() not in seen_lower:
            seen_lower.add(name.lower())
            names.append(name)

    for name, rtype, roff, rdlen in records:
        try:
            if rtype == TYPE_A and rdlen == 4:
                host_addr[name.lower()] = socket.inet_ntoa(data[roff:roff + 4])
            elif rtype == TYPE_SRV and rdlen >= 7:
                _pri, _wt, port = struct.unpack('>HHH', data[roff:roff + 6])
                target = _rd_name(data, roff + 6, rdlen - 6)
                srv[name.lower()] = (target, port)
                add(name)                # PTR 已经收过同一个实例就别再加一次
            elif rtype == TYPE_PTR:
                add(_rd_name(data, roff, rdlen))
        except ValueError:
            continue
    out = []
    for instance in names:
        host, port = srv.get(instance.lower(), ('', 0))
        addr = host_addr.get(host.lower(), '')
        out.append({'name': instance, 'host': host, 'port': port, 'addr': addr})
    return [o for o in out if o['name']]


_LOCAL_IPS_CACHE = None


def _local_ips():
    """本机的 IPv4 地址（用来给每个网卡都加一次组播加入）。

    **别用 socket.getaddrinfo(socket.gethostname())**：那是一次 DNS 解析，有的机器
    （尤其是没连网/DNS 慢的时候）会卡几十秒——扫码配对曾经就卡死在这里，界面一直
    「等待设备扫码」。改成 UDP「连一下」取本端地址：不发包、不解析，纳秒级。
    结果缓存起来，第二次调用不再碰 socket。
    """
    global _LOCAL_IPS_CACHE
    if _LOCAL_IPS_CACHE is not None:
        return _LOCAL_IPS_CACHE
    ips = []
    for dest, port in ((MDNS_ADDR, MDNS_PORT), ('223.5.5.5', 53)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((dest, port))
                ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            continue
        if ip and not ip.startswith('127.') and ip not in ips:
            ips.append(ip)
    _LOCAL_IPS_CACHE = ips
    return ips


def browse(service_type, timeout=4.0, sock=None, now=time.time, targets=None):
    """问一次（最多问两次）mDNS，返回发现的服务列表。

    timeout: 总等待秒数；手机广播只在某个页面打开期间存在，所以要给足几秒。
    sock:    测试可以塞假 socket（只需要 sendto / recvfrom / setsockopt / close）。
    targets: 额外单播问这些 IP（默认只问 224.0.0.251 组播）。
             实测：安卓 NsdManager 广播的 `_adb-tls-pairing._tcp` 对组播查询不回，
             直接单播问到手机 ip:5353 才能拿到端口 —— 所以扫码配对必须带 targets。
    """
    query = build_query(service_type)
    peers = [t for t in (targets or []) if t]
    own = sock is None
    if own:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('', 0))
            for ip in _local_ips():
                try:
                    mreq = socket.inet_aton(MDNS_ADDR) + socket.inet_aton(ip)
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                except OSError:
                    pass
        except OSError:
            s.close()
            return []
    else:
        s = sock
    deadline = now() + max(0.2, timeout)
    next_send = now()
    seen = {}
    try:
        while now() < deadline:
            if now() >= next_send:
                # 每个目标单独 try：一个地址非法/发不出去，不能连坐后面的目标
                for dest in [(MDNS_ADDR, MDNS_PORT)] + [(ip, MDNS_PORT) for ip in peers]:
                    try:
                        s.sendto(query, dest)
                    except OSError:
                        continue
                next_send = now() + 1.0
            s.settimeout(max(0.05, min(0.3, deadline - now())))
            try:
                data, src = s.recvfrom(9000)
            except (socket.timeout, OSError):
                continue
            src_ip = src[0] if src and src[0] else ''
            try:
                records = parse_message(data)
            except ValueError:
                continue
            for item in collect(records, data):
                # 单播问 ip:5353 时，回包的源地址就是那台设备；SRV 包通常不带 A 记录，
                # 而 A 记录又常单独成包——用源地址兜底，否则「有端口没 IP」会被判成没发现
                if not item.get('addr'):
                    item['addr'] = src_ip
                key = item['name'].lower()
                old = seen.get(key)
                # 同一个实例可能先来 SRV 再来 A：合并，别丢 ip
                if old:
                    if not item.get('addr'):
                        item['addr'] = old.get('addr') or ''
                    if not item.get('port'):
                        item['port'] = old.get('port') or 0
                        item['host'] = old.get('host') or ''
                if item.get('port') or item.get('addr'):
                    seen[key] = item
    finally:
        if own:
            try:
                s.close()
            except OSError:
                pass
    return [seen[k] for k in seen]
