# -*- coding: utf-8 -*-
"""一键出安装包：PyInstaller 打包 → 铺到 tools\\魔芋 → Inno Setup 编译 installer\\moyu.iss。

用法（仓库根目录）：
    python tools\\build_installer.py            # 全流程
    python tools\\build_installer.py --skip-pyinstaller   # 只编安装器

产物：
    tools\\魔芋\\魔芋.exe                 （onedir 绿色包，可直接 zip 分发）
    dist-installer\\魔芋-Setup-<版本>.exe  （安装包）
"""

import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
DIST = os.path.join(TOOLS, 'dist')
BUILD = os.path.join(TOOLS, 'build')
DEPLOY = os.path.join(TOOLS, '魔芋')
ISS = os.path.join(ROOT, 'installer', 'moyu.iss')
ISCC_CANDIDATES = [
    os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                 'Inno Setup 6', 'ISCC.exe'),
    os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Inno Setup 6', 'ISCC.exe'),
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Inno Setup 6', 'ISCC.exe'),
]


def read_version():
    try:
        with open(os.path.join(TOOLS, 'version.txt'), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def run(cmd, cwd):
    print('+', ' '.join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def build_exe():
    if run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
            '--distpath', DIST, '--workpath', BUILD, 'Moyu.spec'], TOOLS) != 0:
        return False
    src = os.path.join(DIST, 'Moyu')
    if not os.path.isdir(src):
        print('打包失败：没找到 %s' % src)
        return False
    # 整目录替换（先删再拷，避免 _internal\_internal 套娃）
    if os.path.isdir(DEPLOY):
        shutil.rmtree(DEPLOY, ignore_errors=True)
    shutil.copytree(src, DEPLOY)
    built, want = os.path.join(DEPLOY, 'Moyu.exe'), os.path.join(DEPLOY, '魔芋.exe')
    if os.path.exists(built):
        os.replace(built, want)
    print('已铺到 %s' % DEPLOY)
    return os.path.exists(want)


def find_iscc():
    for p in ISCC_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return ''


def build_installer(version):
    iscc = find_iscc()
    if not iscc:
        print('没找到 ISCC.exe（Inno Setup 6）。先装一次：')
        print('    winget install --id JRSoftware.InnoSetup -e')
        print('装完重跑本脚本即可（只需编安装器时可加 --skip-pyinstaller）。')
        return False
    if not os.path.exists(os.path.join(DEPLOY, '魔芋.exe')):
        print('缺 %s，先跑完整的打包流程。' % os.path.join(DEPLOY, '魔芋.exe'))
        return False
    return run([iscc, '/Qp', '/DMyAppVersion=%s' % version, ISS], ROOT) == 0


def main():
    skip = '--skip-pyinstaller' in sys.argv
    version = read_version()
    print('版本号（tools/version.txt）：%s' % (version or '(缺失)'))
    if not version:
        print('先写 tools/version.txt，例如 1.0.0')
        return 1
    if not skip and not build_exe():
        return 1
    if not build_installer(version):
        return 1
    target = os.path.join(ROOT, 'dist-installer', '魔芋-Setup-%s.exe' % version)
    print('\n完成：%s' % target if os.path.exists(target) else '\n编译命令已执行完，请检查 dist-installer 目录。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
