# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['mirror_app.py'],
    pathex=[],
    binaries=[],
    datas=[('app.ico', '.'), ('app_icon.png', '.'),
           # 版本号单一来源：程序启动时读它（tools/version.txt），安装器编译时也用它
           ('version.txt', '.'),
           # 默认配置随包走：打包版首次运行会把它当种子复制到 %APPDATA%\魔芋\config.json
           ('config.json', '.'),
           # 把 adb/scrcpy 一起打进包：换台机器解压就能用，不再依赖 D:\moyu\tools
           ('scrcpy-win64-v5.0.1/*', 'scrcpy')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Moyu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 故意关掉 UPX：压缩后的 exe 更容易被杀软误报（本机本来也没装 UPX）
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Moyu',
)
