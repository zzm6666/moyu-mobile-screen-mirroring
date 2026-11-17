# CONTEXT — 魔芋（moyu）领域术语表

> 本文件是项目的共享词汇表（domain model）。代码、注释、UI 文案、issue 讨论统一用这里的词，避免同义词漂移。
> 更新规则：改代码引入新概念时同步改这里；发现旧词与新词混用时以本表为准。

## 设备连接（ADB）

| 术语 | 定义 | 不是什么 |
|---|---|---|
| **配对（pair）** | `adb pair ip:配对端口` + 6 位配对码，SPAKE2 认证，在电脑与手机间建立一次性信任。配对端口与连接端口**不同**，且每次打开「无线调试」页面都会变。 | 不是连接；配对成功不代表能投屏 |
| **连接（connect）** | `adb connect ip:连接端口`，建立 ADB 传输通道。已配对的设备每次端口变化后都要重新 connect，但**不需要重新 pair**。 | 不是配对 |
| **串号（serial）** | `adb devices` 第一列的设备标识。USB 是硬件序列号（如 `ABC123`）；无线有两种：`ip:端口`（如 `192.168.1.5:41001`）和 mDNS 串号（如 `adb-bfd7e543-MoDo9u._adb-tls-connect._tcp`）。 | mDNS 串号不是设备名，绝不能直接显示给用户 |
| **在线（online）** | `adb devices` 里状态为 `device` 的条目（`offline`/`unauthorized` 不算）。代码：`connect_ui.parse_devices_l` / `mirror_app.list_devices`。向导 UI 统一显示为「已连接」。 | — |
| **发现（found）** | `adb mdns services` 里 `_adb-tls-connect._tcp` 服务宣告的设备：已配对、当前不在线，但能拿到最新连接端口。 | 不是「未配对的新设备」 |
| **历史设备（known device）** | 本机成功连接过、记在 `config.json → known_devices` 的设备：`{serial, name, addr, last_seen}`，新在前、最多 8 台、按 serial+同 IP 去重。端口过期后显示为「离线」行，点连接走 `reconnect_known`。 | 不等于「已配对设备」——adb 没有列出配对记录的 API，历史表只是本机经验 |
| **设备名（display name）** | 给用户看的名字，取值优先级：营销名（`ro.product.marketname` / `ro.vendor.oplus.market.name` 等，如「一加 13」）→ `adb devices -l` 的 model → `无线设备 <ip>` 兜底。代码：`connect_ui.device_display_name`。 | 不是 serial、不是 mDNS 名 |
| **重连（reconnect）** | 对历史/发现设备：mDNS 串号已在线→直接用；否则先试旧 `addr` connect，再按 mDNS 找同 IP 新端口 connect。代码：`connect_ui.reconnect_known`。 | 不需要重新配对 |
| **自动连接（auto connect）** | 启动时 `try_auto_connect`：在线设备 → 配置串号 ip:端口 → mDNS 发现，三步兜底；全失败才弹向导。 | — |
| **一键连接** | 向导里唯一的「连接手机」按钮：输入 IP:配对端口 + 配对码后，`pair_then_connect` 自动完成 pair → 找连接端口 → connect，用户无感。首次配对必须人工读手机上的 6 位码（协议限制）。 | — |

## 投屏（scrcpy）

| 术语 | 定义 |
|---|---|
| **主窗口（frame）** | 魔芋自绘的冷灰操作栏窗口，scrcpy 画面嵌在其客户区。 |
| **子窗口（child）** | scrcpy 进程的视频窗口，被 SetParent 进 frame；`--window-title` 固定为配置 title。 |
| **watchdog** | 每秒一次的 WM_TIMER 检查：收养 child、跟随宽高比、检测 scrcpy 退出重试（≤2 次）、60 秒无窗口报错。 |
| **等待计时（started_at）** | 60 秒超时的起点 = **本次 scrcpy 进程 Popen 时刻**（`start_scrcpy` 重置），不是程序启动时刻。配对/向导停留耗时不计入。 |
| **掉线重试（retries）** | scrcpy 进程退出且设备仍在线时自动重拉，最多 2 次；向导成功连接会清零。 |

## 配置与发版

- `config.json`：用户数据（serial、窗口几何、known_devices…），打包版在 `%APPDATA%\魔芋\`，源码期在 `tools/`。`DEFAULT_CFG`（mirror_app.py）是缺省值，`load_cfg` 用文件覆盖。
- `version.txt`：当前版本号；发版走 `tools/release.py` → PyInstaller（`tools/Moyu.spec`）→ `tools/build_installer.py`（Inno Setup `installer/moyu.iss`）。
- 错误文案原则：**用户可见的错误一律通俗中文 + 下一步建议**（`connect_ui.friendly_error` 规则表）；adb 英文原文只进 `app.log`，不进 UI。