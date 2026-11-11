# -*- coding: utf-8 -*-
"""版本号与自动更新（查 GitHub Releases、选安装包、下载）。

只用标准库；网络访问全部通过可注入的 opener，方便离线测试。
"""

import json
import os
import re
import urllib.error
import urllib.request

API = 'https://api.github.com/repos/%s/releases/latest'
# API 有匿名限流（60 次/小时/IP，超了直接 403）。兜底走网页跳转：带着 HEAD 请求
# releases/latest，GitHub 会 302 到 /releases/tag/vX.Y.Z，从落地地址取版本号即可，
# 资产地址按我们的发布约定拼出来（发布时附件名固定是 Moyu-Setup-<版本>.exe）。
LATEST_PAGE = 'https://github.com/%s/releases/latest'
ASSET_URL = 'https://github.com/%s/releases/download/v%s/Moyu-Setup-%s.exe'
_TAG_IN_URL = re.compile(r'/releases/tag/v?([0-9][0-9A-Za-z.\-]*)')
TIMEOUT = 8
UA = 'Moyu-Updater'

# 版本号：主.次.修订，可选 -预发布标签（如 1.2.0-beta.1）
_VER_RE = re.compile(r'^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+]([0-9A-Za-z.\-]+))?$')


def parse_version(text):
    """把 "v1.2.3" / "1.2" / "1.2.3-beta.1" 解析成 (1,2,3,'beta.1')；无法解析返回 ()。"""
    if not isinstance(text, str):
        return ()
    m = _VER_RE.match(text.strip())
    if not m:
        return ()
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    pre = m.group(4) or ''
    return (major, minor, patch, pre)


def is_newer(latest, current):
    """latest 是否比 current 新。任一无法解析则返回 False（宁可不动）。"""
    a = parse_version(latest)
    b = parse_version(current)
    if not a or not b:
        return False
    if a[:3] != b[:3]:
        return a[:3] > b[:3]
    # 主次修订相同：正式版比预发布新；同为预发布时按字符串比
    if a[3] == b[3]:
        return False
    if not a[3]:
        return True
    if not b[3]:
        return False
    return a[3] > b[3]


def pick_asset(release, want='-setup.exe'):
    """从 release JSON 里挑安装包直链；优先匹配后缀，退而求其次取第一个 .exe。"""
    assets = (release or {}).get('assets') or []
    fallback = ''
    for a in assets:
        name = (a.get('name') or '')
        url = (a.get('browser_download_url') or '')
        if not url:
            continue
        low = name.lower()
        if low.endswith(want) or ('setup' in low and low.endswith('.exe')):
            return url
        if not fallback and low.endswith('.exe'):
            fallback = url
    return fallback


def release_info(release):
    """把 GitHub 的 release 归一成程序内部用的小字典。"""
    rel = release or {}
    tag = rel.get('tag_name') or ''
    ver = tag.lstrip('vV') or tag
    return {
        'version': ver,
        'url': pick_asset(rel),
        'page': rel.get('html_url') or '',
        'notes': (rel.get('body') or '')[:2000],
    }


def resolve_by_redirect(repo, opener=None, timeout=TIMEOUT):
    """不走 API 的兜底：HEAD releases/latest，从跳转后的地址里取版本号。

    API 匿名限流时（HTTP 403）靠这条也能发现新版本，代价是拿不到发布说明、
    资产直链只能按发布约定拼。
    """
    open_url = opener or urllib.request.urlopen
    req = urllib.request.Request(LATEST_PAGE % repo, headers={'User-Agent': UA}, method='HEAD')
    try:
        with open_url(req, timeout=timeout) as resp:
            final = resp.geturl() or ''
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, e)
    m = _TAG_IN_URL.search(final)
    if not m:
        return None, '跳转地址里没有版本号'
    ver = m.group(1)
    return {
        'version': ver,
        'url': ASSET_URL % (repo, ver, ver),
        'page': final,
        'notes': '',
    }, ''


def fetch_latest(repo, opener=None, timeout=TIMEOUT):
    """查询最新 release，返回 (info, err)；err 为空表示成功。"""
    repo = (repo or '').strip()
    if not repo or '/' not in repo:
        return None, '仓库未配置'
    req = urllib.request.Request(API % repo, headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        api_err = 'HTTP %s' % e.code
    except Exception as e:                      # 断网/超时/证书问题都归到这里
        api_err = '%s: %s' % (type(e).__name__, e)
    else:
        info = release_info(data)
        if not info['version']:
            return None, '发布里没有版本号'
        return info, ''
    # API 失败：换成网页跳转再试一次（匿名限流 403 也能拿到版本）
    info, red_err = resolve_by_redirect(repo, opener=opener, timeout=timeout)
    if info:
        return info, ''
    return None, api_err if api_err else red_err


def download(url, dest, opener=None, timeout=60):
    """下载到 dest（先写 .part 再改名，避免半截文件被当成安装包）。"""
    open_url = opener or urllib.request.urlopen
    tmp = dest + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with open_url(req, timeout=timeout) as resp, open(tmp, 'wb') as f:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)
        return True, ''
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, '%s: %s' % (type(e).__name__, e)
