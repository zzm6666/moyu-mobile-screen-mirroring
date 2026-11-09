# 魔芋 · Windows 手机投屏小窗

把 Android 手机屏幕变成贴在桌面上的一小块窗口：半透明操作栏、圆角、窗边淡阴影，可拖动可缩放，鼠标直接操作手机（反控）。

底层是 [scrcpy](https://github.com/Genymobile/scrcpy) + `adb`，界面部分**纯 ctypes 调用 Win32 + Pillow 绘制**，不依赖任何 GUI 框架。

![界面示意](tools/app_icon.png)

## 功能

- 无边框小窗投屏，默认 60 fps，视频区不留黑边
- 白色半透明操作栏：应用圆形图标 + 标题 + 关闭按钮（点关闭收进托盘，不是退出）
- 整窗四角圆角，边框外一圈淡阴影（不压到视频区）
- 拖操作栏移动窗口；拖四边/四角缩放，锁手机原始宽高比，不越界、贴到屏幕底部时自动上移让位
- 手机横竖屏切换时窗口跟随，初始位置在屏幕右下角
- 托盘单击显示/隐藏窗口；无任务栏按钮、不抢焦点
- 其他窗口最小化时不跳层抢前台，只把原本压在它上面的位次补回来
- 连接向导：USB 直连，或「无线调试」配对码配对；之后启动自动重连

## 环境要求

| 项 | 说明 |
|---|---|
| 系统 | Windows 10 / 11 x64 |
| Python | 3.9+（仅源码运行或自行打包时需要） |
| 依赖 | `pip install pillow` |
| scrcpy | 5.0.1（打包时内置进安装目录） |
| 手机 | Android，需开启「开发者选项 → 无线调试」或「USB 调试」 |

## 准备 scrcpy

从 <https://github.com/Genymobile/scrcpy/releases> 下载 **scrcpy-win64-v5.0.1**，解压成：

```
tools/scrcpy-win64-v5.0.1/scrcpy.exe
tools/scrcpy-win64-v5.0.1/adb.exe
```

该目录体积约 26 MB，属于第三方发行物，因此不随仓库分发。

## 源码运行

```bat
pip install pillow
tools\dev.bat
```

首次启动如果没有可用设备，会弹出连接向导。

## 打包（onedir 绿色包）

```bat
pyinstaller --noconfirm --distpath tools\dist --workpath tools\build tools\Moyu.spec
```

产物在 `tools/dist/Moyu/`，把里面的内容整体拷到一个目录、把 `Moyu.exe` 改成 `魔芋.exe` 即可（自带 `_internal/scrcpy`，zip 发给别人解压就能用）。

## 连接手机

**无线（推荐，一次配对）**

1. 手机：设置 → 关于手机 → 连点「版本号」7 次，回到设置 → 开发者选项 → 打开「无线调试」
2. 进入「无线调试」→「使用配对码配对设备」，得到 `IP:端口` 和 6 位配对码
3. 托盘右键 →「连接手机…」→ 填 `IP:端口` + 配对码 → 点「配对」，配对成功后再点「连接」

> 配对端口和连接端口**不是同一个端口**。配对成功后，程序会自动从 `adb mdns services` 里找到真正的连接端口；万一网络屏蔽了 mDNS，就手动填无线调试页面上那个「IP 地址和端口」点「连接」。

**USB**

插线 → 手机上点「允许 USB 调试」→ 在向导里选设备点「用这台设备」。

**日常启动**：自动按「在线设备 → 上次的地址 → mDNS 发现」的顺序重连，一般不需要任何输入。手机关机或重启后，系统会把「无线调试」开关关闭，重新打开即可（不用重新配对）。

## 配置文件与日志

- 配置：`%APPDATA%\魔芋\config.json`（首次运行时从 exe 同目录的 `config.json` 复制一份当种子）
- 日志：`%APPDATA%\魔芋\app.log`

```json
{
  "serial": "",         // 留空 = 自动查找在线设备
  "title": "魔芋",
  "x": 1636, "y": 448,
  "w": 260, "h": 572,
  "fps": 60,
  "bitrate": "4M",
  "max_size": 720
}
```

## 项目结构

```
moyu/
├─ tools/
│  ├─ mirror_app.py            主程序：窗口、绘制、托盘、缩放、连接逻辑
│  ├─ connect_ui.py            连接向导（纯 ctypes 实现的对话框）
│  ├─ Moyu.spec                PyInstaller 打包配置
│  ├─ config.json              默认配置（首次运行复制到 %APPDATA%）
│  ├─ make_icon.py             app_256.png → 圆形 app_icon.png / app.ico
│  ├─ app_256.png              图标源图
│  ├─ app_icon.png             操作栏用的圆形图标
│  ├─ app.ico                  exe 与托盘图标
│  ├─ dev.bat                  源码方式启动
│  └─ scrcpy-win64-v5.0.1/     第三方 scrcpy（自行下载）
└─ .gitignore
```

## 已知限制

- **必须开开发者选项**：`adb` 权限只在开发者选项里（USB 调试 / 无线调试），任何反控方案都绕不开。
- 下两个圆角用 `SetWindowRgn` 做二值裁剪，没有抗锯齿；上两角走 alpha 合成，有抗锯齿。
- 新构建的 exe 首次启动可能被 Defender 实时扫描，卡 60~90 秒属于正常现象。
- 操作栏和视频是两个独立窗口，极端场景下仍有亚毫秒级的不同步。

## 技术要点

- 分层窗口 `UpdateLayeredWindow` 画操作栏与阴影，视频子窗口用 `SetWindowRgn` 裁圆角
- 视频窗口外扩 overscan + scrcpy `--render-fit=stretched`，消除整数缩放留下的 1~2 px 黑边
- 全局低级鼠标钩子处理边缘缩放：视频窗口盖住了主窗口的边，`WM_NCHITTEST` 收不到那些点
- 鼠标注入用来做自动化验收（`mouse_event` 的 `LEFTDOWN=0x0002` / `LEFTUP=0x0004`）
