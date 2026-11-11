# -*- coding: utf-8 -*-
"""一键发版：编译 → 检查版本 → 打 tag → 建/更新 GitHub Release → 上传附件。

用法（仓库根目录，或在任意目录指定路径）：
    python tools\\release.py                # 用 tools/version.txt 里的版本号
    python tools\\release.py 1.0.3          # 顺便把版本号写成 1.0.3 再发
    python tools\\release.py --skip-build   # 不发编译，直接发布已有产物
    python tools\\release.py --notes-file notes.md   # 自定义发布说明

发布说明默认按版本从 tools/release_notes.md 里取同名小节（## v1.0.3）。
中文一律用 UTF-8 字节发送 —— 之前用 PowerShell 发 Release，body 里的中文全被
写成了 "?"，就是因为 5.1 的 Invoke-RestMethod 把字符串按 ISO-8859-1 发了出去。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
REPO_SLUG = 'zzm6666/moyu-mobile-screen-mirroring'
NOTES_FILE = os.path.join(TOOLS, 'release_notes.md')
DIST_INSTALLER = os.path.join(ROOT, 'dist-installer')
API = 'https://api.github.com/repos/%s' % REPO_SLUG


def read_version():
    try:
        with open(os.path.join(TOOLS, 'version.txt'), 'r', encoding='utf-8') as f:
            return f.read().strip().lstrip('\ufeff').strip()
    except OSError:
        return ''


def write_version(ver):
    with open(os.path.join(TOOLS, 'version.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(ver + '\n')


def run(args, cwd=ROOT, tries=4, pause=6):
    """跑一条命令；网络抖动时重试几次（github.com 经常 Connection was reset）。"""
    import time as _time
    print('+ ' + ' '.join(args))
    for i in range(tries):
        if subprocess.call(args, cwd=cwd) == 0:
            return True
        if i + 1 < tries:
            _time.sleep(pause)
    return False


def remote_tag_sha(tag, tok):
    """远端 tag 指向的 commit（拿不到返回 ''）。"""
    try:
        ref = api('GET', '/git/ref/tags/' + tag, tok=tok)
    except urllib.error.HTTPError:
        return ''
    obj = ref.get('object') or {}
    if obj.get('type') == 'tag':            # 附注 tag 要再解一层
        try:
            tagobj = api('GET', '/git/tags/' + obj['sha'], tok=tok)
            obj = tagobj.get('object') or {}
        except urllib.error.HTTPError:
            return ''
    return obj.get('sha') or ''


def token():
    """从 Git Credential Manager 取 GitHub token（和 git push 用的是同一个）。"""
    out = subprocess.run(['git', 'credential', 'fill'], cwd=ROOT, capture_output=True,
                         text=True, input='protocol=https\nhost=github.com\n\n').stdout
    for line in out.splitlines():
        if line.startswith('password='):
            return line[len('password='):].strip()
    return ''


def api(method, path, payload=None, tok=''):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        'Authorization': 'token ' + tok,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Moyu-Release',
        'Content-Type': 'application/json; charset=utf-8',
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    # DELETE 资产返回 204 无内容，别拿空串去 json.loads
    return json.loads(raw.decode('utf-8')) if raw.strip() else {}


def default_notes(ver):
    """发布说明：优先取 release_notes.md 里 ## v<版本> 那一段，否则用模板。"""
    try:
        with open(NOTES_FILE, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        text = ''
    m = re.search(r'^##\s*v?%s\s*$(.*?)(?=^##\s|\Z)' % re.escape(ver),
                  text, re.M | re.S)
    body = (m.group(1).strip() if m else '')
    if body:
        return body
    return ('安装与连接手机的步骤见 README：https://github.com/%s#readme\n\n'
            '下载：\n'
            '- `Moyu-Setup-%s.exe`：安装版（装到当前用户目录，可卸载）\n'
            '- `Moyu-%s-portable.zip`：便携版，解压后双击 `安装.cmd` 或直接运行 `魔芋.exe`\n'
            % (REPO_SLUG, ver, ver))


def upload(rel_id, path, tok):
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        data = f.read()
    req = urllib.request.Request(
        'https://uploads.github.com/repos/%s/releases/%d/assets?name=%s' % (REPO_SLUG, rel_id, name),
        data=data, method='POST', headers={
            'Authorization': 'token ' + tok,
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'Moyu-Release',
            'Content-Type': 'application/octet-stream',
        })
    with urllib.request.urlopen(req, timeout=900) as resp:
        got = json.loads(resp.read().decode('utf-8'))
    print('已上传 %s（%d 字节）' % (got['name'], got['size']))
    return got


def main():
    argv = [a for a in sys.argv[1:]]
    skip_build = '--skip-build' in argv
    argv = [a for a in argv if a != '--skip-build']
    notes_file = ''
    if '--notes-file' in argv:
        i = argv.index('--notes-file')
        notes_file = argv[i + 1] if i + 1 < len(argv) else ''
        del argv[i:i + 2]

    if argv and re.match(r'^\d+\.\d+\.\d+', argv[0]):
        write_version(argv[0])
        print('版本号已写入 tools/version.txt：%s' % argv[0])

    ver = read_version()
    if not ver:
        print('缺少 tools/version.txt')
        return 1
    print('版本：%s' % ver)

    if not skip_build:
        if not run([sys.executable, os.path.join(TOOLS, 'build_installer.py')]):
            return 1

    setup = os.path.join(DIST_INSTALLER, 'Moyu-Setup-%s.exe' % ver)
    portable = os.path.join(DIST_INSTALLER, 'Moyu-%s-portable.zip' % ver)
    for p in (setup, portable):
        if not os.path.exists(p):
            print('缺产物：%s（先跑 tools\\build_installer.py）' % p)
            return 1

    body = default_notes(ver)
    if notes_file:
        with open(notes_file, 'r', encoding='utf-8') as f:
            body = f.read().strip()

    tag = 'v' + ver
    if not run(['git', 'tag', '-f', tag, 'HEAD']):
        return 1

    tok = token()
    if not tok:
        print('没拿到 GitHub token（git credential 里没有 github.com 的凭据）')
        return 1

    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    if not run(['git', 'push', '-f', 'origin', 'refs/tags/' + tag]):
        remote = remote_tag_sha(tag, tok)
        if remote and remote.startswith(head[:12]):
            print('提醒：推送 tag 时网络失败，但远端已有 %s（%s），继续发布。' % (tag, remote[:8]))
        else:
            print('推送 tag 失败，且远端没有指向 %s 的 %s，先解决网络再发。' % (head[:8], tag))
            return 1

    name = '魔芋 %s' % ver
    payload = {'tag_name': tag, 'name': name, 'body': body,
               'draft': False, 'prerelease': '-' in ver}
    try:
        rel = api('GET', '/releases/tags/' + tag, tok=tok)
        print('Release 已存在，更新标题与说明：%s' % rel['html_url'])
        rel = api('PATCH', '/releases/%d' % rel['id'], payload, tok)
        for a in rel.get('assets') or []:
            api('DELETE', '/releases/assets/%d' % a['id'], tok=tok)
            print('清掉旧附件 %s' % a['name'])
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        payload['target_commitish'] = 'master'
        rel = api('POST', '/releases', payload, tok)
        print('已创建 Release：%s' % rel['html_url'])

    upload(rel['id'], setup, tok)
    upload(rel['id'], portable, tok)
    print('\n完成：%s' % rel['html_url'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
