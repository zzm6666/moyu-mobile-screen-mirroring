# 发布说明

每个版本的说明都写在这里，`tools/release.py` 发版时会取同名小节（`## v<版本>`）当 Release 正文。
上面这段是文件说明，不会出现在 Release 里。

## v1.0.2

修复：点击最小化别的应用窗口时，魔芋的窗口偶尔会被系统激活的窗口盖住（层级下降）。
现在最小化别的窗口时，魔芋会留在原来的位置，不会被盖住，也不会跳到最前抢焦点。

另外两处稳定性改进：

- 自动更新在 GitHub 接口被限流（HTTP 403）时不再静默失效，会改走发布页兜底检查。
- 版本号文件里混入 UTF-8 BOM 时不再让打包脚本报错。

安装与连接手机的步骤见 README：https://github.com/zzm6666/moyu-mobile-screen-mirroring#readme

下载：

- `Moyu-Setup-1.0.2.exe`：安装版（装到当前用户目录，可卸载）
- `Moyu-1.0.2-portable.zip`：便携版，解压后双击 `安装.cmd` 或直接运行 `魔芋.exe`

## v1.0.1

修复：点击最小化别的应用窗口时，魔芋会短暂跳到最前又掉回去（闪一下）。

安装与连接手机的步骤见 README：https://github.com/zzm6666/moyu-mobile-screen-mirroring#readme

下载：

- `Moyu-Setup-1.0.1.exe`：安装版（装到当前用户目录，可卸载）
- `Moyu-1.0.1-portable.zip`：便携版，解压后双击 `安装.cmd` 或直接运行 `魔芋.exe`

## v1.0.0

首个版本：把手机画面投到桌面小窗，可拖动、可缩放、可鼠标反控，支持无线连接与自动重连。

安装与连接手机的步骤见 README：https://github.com/zzm6666/moyu-mobile-screen-mirroring#readme
