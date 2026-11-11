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

## 版本号规则

版本号只有一个来源：**`tools/version.txt`**（一行，例如 `1.0.0`）。

- 程序启动时读它，日志第一行会打印 `--- start, v1.0.0 ...`，托盘「检查更新…」也用它比较
- 打包时它被复制进 `_internal/version.txt`；编译安装器时由 `tools/build_installer.py` 注入 `{#MyAppVersion}`
- 发版前只改这一个文件，然后打 tag、出安装包

遵循 [语义化版本](https://semver.org/lang/zh-CN/) `主.次.修订`：

| 变更 | 例子 |
|---|---|
| 主版本：不兼容的改动（配置格式、连接方式大改） | `1.0.0 → 2.0.0` |
| 次版本：新增功能，向后兼容 | `1.0.0 → 1.1.0` |
| 修订：只修 bug | `1.0.0 → 1.0.1` |

预发布可写 `1.0.0-beta.1`（比较时小于同号正式版）。

## 构建安装包

```bat
python tools\build_installer.py
```

一条命令做四件事：读 `tools/version.txt` → PyInstaller 打包 → 铺到 `tools\魔芋\` → 调 Inno Setup 编译 `installer\moyu.iss`。

产物：

- `dist-installer\魔芋-Setup-<版本>.exe` —— 标准安装包（Inno Setup）
- `dist-installer\魔芋-<版本>-portable.zip` —— 便携版（解压即用，内含 `install.ps1` 脚本安装器）

- 默认装到 `%LOCALAPPDATA%\Programs\魔芋`，**不需要管理员权限**，安装向导里可以改目录
- 自带卸载器；卸载时会问要不要连 `%APPDATA%\魔芋` 里的配置和日志一起删
- 只编安装器（跳过 PyInstaller）：`python tools\build_installer.py --skip-pyinstaller`
- 需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)：`winget install --id JRSoftware.InnoSetup -e`
- 想要中文安装向导：把 `ChineseSimplified.isl` 放进 `installer\` 目录（Inno 官方包不含简体中文），脚本会自动应用

> 没有代码签名证书，别人首次运行安装包时 Windows SmartScreen 会提示「未知发布者」，点「更多信息 → 仍要运行」即可。

### 便携版 / 脚本安装（安装包跑不起来时的兜底）

个别机器上 Inno 的安装程序会在第一步就失败，报：

```
Setup was unable to create the directory "C:\Users\<你>\AppData\Local\Temp\is-XXXX.tmp".
Error 5: 拒绝访问。
```

（实测出现在装过腾讯电脑管家的 Windows 11 上；同一个安装包里的文件搬运本身是好的。）这时用便携版：

1. 解压 `魔芋-<版本>-portable.zip`
2. 双击 `安装.cmd`（或者右键 `install.ps1` → 用 PowerShell 运行）

`install.ps1` 做的事和安装包一样，但不经过 Setup：复制到 `%LOCALAPPDATA%\Programs\魔芋`、建开始菜单/桌面快捷方式、写 HKCU 卸载项（设置 → 应用 里能看到「魔芋」，可卸载）。安装目录里会生成 `卸载.cmd`。

也可以不安装，直接双击解压出来的 `魔芋.exe` 当绿色版用。


### 被安全软件拦截时怎么办

没有签名的安装包容易被安全软件误判，实测（腾讯电脑管家）有两种表现：

- 直接弹「文件包含病毒或潜在的垃圾软件」并把安装包删掉；
- 或者装到一半静默失败，此时用 `/LOG=路径` 重装一次就能看到真实原因，常见的是
  `IPersistFile::Save failed; code 0x80070005`（创建快捷方式被拒）和
  `RegCreateKeyEx failed; code 5`（写卸载信息被拒）。

处理办法：把安装包加入安全软件的信任区/允许运行，或在弹窗里选「允许」后重装；也可以只信任解压出来的 `魔芋.exe`。程序本身不联网写注册表以外的系统位置，配置和日志都在 `%APPDATA%\魔芋`。

安装包和 exe 都可以自查有没有被改过：`certutil -hashfile <文件> SHA256`。

## 自动更新

配置文件里的 `update_repo` 指向 GitHub 仓库（默认 `zzm6666/moyu-mobile-screen-mirroring`）：

- 启动后台检查：距上次检查满 24 小时才查一次，**只有发现新版本才弹窗**，断网/未配置都静默跳过
- 托盘右键「检查更新…」：立即检查，并把失败原因也告诉用户
- 有新版本时：确认 → 下载到临时目录 → 静默启动安装器（安装器会自己关掉正在运行的魔芋）→ 程序退出

发版流程：改 `tools/version.txt` → `python tools\build_installer.py` → 在 GitHub 建一个 **tag 为版本号**（如 `v1.0.1`）的 Release → 把 `魔芋-Setup-<版本>.exe` 传上去当附件。

> 安装包文件名以 `-setup.exe` 结尾会被优先选中；找不到就取 Release 里第一个 `.exe`。

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
  "max_size": 720,
  "update_repo": "zzm6666/moyu-mobile-screen-mirroring",  // 留空 = 不自动检查更新
  "last_update_check": 0                                  // 上次检查时间戳，程序自己维护
}
```

> 配置里出现新字段时会自动补默认值，旧配置不用手动改。

## 项目结构

```
moyu/
├─ LICENSE                       MIT
├─ installer/
│  ├─ moyu.iss                   Inno Setup 安装脚本
│  └─ ChineseSimplified.isl      可选：中文安装向导语言包
├─ tools/
│  ├─ mirror_app.py              主程序：窗口、绘制、托盘、缩放、连接、更新检查
│  ├─ connect_ui.py              连接向导（纯 ctypes 实现的对话框）
│  ├─ updater.py                 版本比较 / 查 GitHub Releases / 下载安装包
│  ├─ build_installer.py         一键出安装包
│  ├─ Moyu.spec                  PyInstaller 打包配置
│  ├─ version.txt                版本号（唯一来源）
│  ├─ config.json                默认配置（首次运行复制到 %APPDATA%）
│  ├─ make_icon.py               app_256.png → 圆形 app_icon.png / app.ico
│  ├─ app_256.png                图标源图
│  ├─ app_icon.png               操作栏用的圆形图标
│  ├─ app.ico                    exe 与托盘图标
│  ├─ dev.bat                    源码方式启动
│  └─ scrcpy-win64-v5.0.1/       第三方 scrcpy（自行下载，打包时内置）
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

## 许可与第三方

- 本项目代码：[MIT](LICENSE)
- [scrcpy](https://github.com/Genymobile/scrcpy)（含 `adb`）：Apache-2.0，安装目录里的 `_internal\scrcpy\LICENSE.txt` 已随包分发
- [Pillow](https://python-pillow.org/)：HPND / MIT-CMU
