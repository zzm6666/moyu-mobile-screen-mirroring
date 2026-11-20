"""GitHub 连不上时，用 API 把本地提交推到远端。

背景：这台机器上 github.com:443 经常被掐（git push / fetch 都是 timeout 或
connection reset），但 api.github.com 一直是通的。于是走 Git Data API：
对每个要推的提交上传改动的 blob、基于父提交的树建新树、建提交、最后把
master 引用指过去——提交说明、作者、时间都保持原样。

用法：
    python tools\\push_via_api.py                 # 基线用 origin/master
    python tools\\push_via_api.py e97cde4         # 远端那段历史是 API 重放的、
                                                # 本地没有对应对象时，指定本地基线

推完会打印「树一致：是/否」。是就说明远端内容和本地 HEAD 完全一样。
之后网络恢复时执行 git fetch origin && git reset --hard origin/master 即可对齐 sha。
"""
import base64
import subprocess
import sys

HERE = __file__.rsplit('\\', 1)[0]
sys.path.insert(0, HERE)
import release                                     # noqa: E402  复用 REPO_SLUG / api / token

REPO_DIR = __file__.rsplit('\\', 2)[0]
tok = release.token()


def api(method, path, payload=None):
    return release.api(method, path, payload, tok=tok)


def git(*args):
    out = subprocess.run(['git'] + list(args), cwd=REPO_DIR, capture_output=True)
    if out.returncode:
        raise SystemExit('git %s 失败：%s'
                         % (' '.join(args), out.stderr.decode('utf-8', 'replace')))
    return out.stdout


def make_tree_entry(commit, path):
    """把某个路径在该提交里的内容做成 tree 条目（blob 先上传）。"""
    line = git('ls-tree', commit, '--', path).decode('utf-8').strip()
    if not line:
        return None
    meta = line.partition('\t')[0]
    mode = meta.split()[0]
    data = git('cat-file', 'blob', '%s:%s' % (commit, path))
    r = api('POST', '/git/blobs',
            {'content': base64.b64encode(data).decode('ascii'), 'encoding': 'base64'})
    return {'path': path, 'mode': mode, 'type': 'blob', 'sha': r['sha']}


def main():
    if not tok:
        print('没拿到 GitHub token（git credential 里没有 github.com 的凭据）')
        return 1
    local_base = sys.argv[1] if len(sys.argv) > 1 else 'origin/master'
    remote = api('GET', '/git/ref/heads/master')['object']['sha']
    head = git('rev-parse', 'HEAD').decode().strip()
    print('远端 master = %s' % remote[:8])
    print('本地 HEAD   = %s（基线 %s）' % (head[:8], local_base))
    if remote == head:
        print('已经一致，什么都不用做')
        return 0

    commits = git('rev-list', '--reverse', '%s..%s' % (local_base, head)).decode().split()
    if not commits:
        print('本地基线之后没有提交，无需推送')
        return 0
    print('要推 %d 个提交' % len(commits))

    parent = remote
    parent_tree = api('GET', '/git/commits/' + remote)['tree']['sha']
    for i, c in enumerate(commits, 1):
        entries = []
        for row in git('diff', '--name-status', '%s^' % c, c).decode('utf-8').splitlines():
            parts = row.split('\t')
            status, path = parts[0], parts[-1]
            if status.startswith('D'):
                entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': None})
                continue
            entry = make_tree_entry(c, path)
            if entry:
                entries.append(entry)
        tree = api('POST', '/git/trees', {'base_tree': parent_tree, 'tree': entries})
        fields = git('log', '-1', '--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%B', c)
        an, ae, ad, cn, ce, cd, msg = fields.decode('utf-8').split('\x00')
        new = api('POST', '/git/commits', {
            'message': msg.rstrip('\n'),
            'tree': tree['sha'],
            'parents': [parent],
            'author': {'name': an, 'email': ae, 'date': ad},
            'committer': {'name': cn, 'email': ce, 'date': cd},
        })
        print('  %d/%d %s → %s  %s' % (i, len(commits), c[:8], new['sha'][:8],
                                       msg.splitlines()[0]))
        parent, parent_tree = new['sha'], tree['sha']

    api('PATCH', '/git/refs/heads/master', {'sha': parent, 'force': False})
    print('master 已指向 %s' % parent[:8])
    print('树一致：%s' % ('是' if git('rev-parse', 'HEAD^{tree}').decode().strip() == parent_tree
                          else '否（远端内容和本地不一样，别急着 reset）'))
    print('REMOTE_HEAD=%s' % parent)
    return 0


if __name__ == '__main__':
    sys.exit(main())
