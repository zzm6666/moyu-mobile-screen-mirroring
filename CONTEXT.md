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
| **发现（found）** | 手机在广播 `_adb-tls-connect._tcp`、但本机现在没有和它连上（`adb devices` 里没有）。可能以前配对过（无线调试关过再开、拔过线），也可能从没跟本机配对过。向导 UI 显示为「新发现」。 | 不是「未配对的新设备」，也不是「一定连得上」 |
| **历史设备（known device）** | 本机成功连接过、记在 `config.json → known_devices` 的设备：`{serial, name, addr, last_seen}`，新在前、最多 8 台、按 serial+同 IP 去重。 | 不等于「已配对设备」——adb 没有列出配对记录的 API，历史表只是本机经验 |
| **设备名（display name）** | 给用户看的名字，取值优先级：营销名（`ro.product.marketname` / `ro.vendor.oplus.market.name` 等，如「一加 13」）→ `adb devices -l` 的 model → `无线设备 <ip>` 兜底。代码：`connect_ui.device_display_name`。 | 不是 serial、不是 mDNS 名 |
| **重连（reconnect）** | 对历史/发现设备：mDNS 串号已在线→直接用；否则先试旧 `addr` connect，再按 mDNS 找同 IP/同服务名的新端口 connect。代码：`connect_ui.reconnect_known`。 | 不需要重新配对；当前 UI 不再直接调用它（离线行没有按钮，手机重新开无线调试后会以「新发现」自己回来） |
| **行内按钮（row action）** | 设备行右侧的按钮，只有「新发现」行才有：「已连接」行不给（点它是重复连接）；「新发现」行按历史分——本机连过（`connect_ui._seen_before`，按串号或 IP 命中 `known_devices`）显示「连接」，从没连过显示「配对」。点「配对」展开配对面板、只填该行 **IP**（绝不填端口：列表里的是连接端口，配对端口是手机配对页上另一个随机端口，配对时由 `discover_pair_target` 现问），焦点交给配对码框，用户只需填 6 位码。 | 按钮文案不是装饰：它是「这台设备还需不需要配对」的唯一提示 |
| **离线（offline）** | 既不在 `adb devices`、也没有任何 mDNS 广播（多半手机没开无线调试）。**不进设备列表**：此刻既连不上也配不了，只会剩个死按钮；手机一开无线调试就会以「新发现」重新出现。列表空着时若历史里有名字，空态提示「上次连过的「X」现在没开着无线调试」（`connect_ui._last_known_name`）。数据层 `merge_devices` 仍产出 offline 行（供去重与名字用），只在 `connect_ui._list_rows` 里过滤掉。 | 用户 m04505：离线状态本身没有存在意义 |
| **配对面板（pair panel）** | 窗口下方那块填配对码的区域，平时收起（`ctx['pair_open']=False`），只在列表下方留一行「设备不在列表里？手动配对」；一台设备都没发现时**自动展开**一次（`ctx['auto_opened']`）。展开方式三种：`_set_pair_open(ctx, '')` 收起 / `'prefill'` 只显示配对码框（自动问到的 IP:端口 以小字显示在框正上方，不占输入框）/ `'manual'` 三个框都显示，且会**清空**上次留下的 IP/端口。窗口高度跟着面板走（`_win_h`：收起 508 / 展开 560，`_fit_client` 才把含边框的 SetWindowPos 尺寸换算成客户区）。代码：`connect_ui._set_pair_open`、`_prefill_pair`、`_fit_client`。 | prefill 不是「不让用户改地址」，是「地址已经问过设备了」；手动模式清空是为了不让人照着旧地址填错 |
| **列表滚动（list scroll）** | 列表卡片**高度固定**：收起配对面板时 6 行（`MAX_ROWS_FULL`），展开时 4 行（`MAX_ROWS`）；设备更多时用滚轮翻（`ctx['scroll']`，每次 1 行，右侧画细滚动条）。`_visible_rows` 给绘制和热点同一段窗口。 | 高度固定是为了窗口不长高、按钮位置不跳 |
| **自动连接（auto connect）** | 启动时 `try_auto_connect`：在线设备 → 配置串号 ip:端口 → mDNS 发现，三步兜底；全失败才弹向导。 | — |
| **自动刷新（auto refresh）** | 向导开着时每 3 秒（`connect_ui.AUTO_REFRESH_MS`，WM_TIMER id 3）悄悄刷一次设备列表：不转圈、不写日志；**完全不碰状态行**（`_apply_job` 里只有非 auto 才清状态），用户看到的错误/操作提示不会被它清掉。手机重开无线调试后几秒内自己变回「新发现/已连接」。 | 不是「刷新按钮的自动化替代」——手动刷新仍会显示进度与结果 |
| **连接端口广播** | 连接端口是随机端口，电脑只能靠手机的 mDNS 广播（`_adb-tls-connect._tcp`）发现它。广播可能迟到；路由器开了客户端隔离/跨网段时**永远收不到**——此时只能让用户把「无线调试」主页上的端口手动填进「端口」框。代码：`connect_ui.parse_mdns_services`。 | 不是配对端口的别名；也不是能扫描出来的固定端口 |
| **配对端口广播** | 手机停在「使用配对码配对设备」页面（或扫码配对页）时会广播 `_adb-tls-pairing._tcp`，其中带着**配对端口**。安卓的 NsdManager 广播**不回组播查询**，必须单播问手机 `ip:5353` 才拿得到（实测组播 100 轮 0 条、单播立刻命中）。代码：`connect_ui.discover_pair_target` + `mdns_browse.browse(..., targets=[ip])`。 | 不是连接端口；组播问不到不代表手机没广播 |
| **一键连接** | 向导里唯一的「连接手机」按钮，`connect_ui.decide_action` 分三路：只填 6 位配对码 → 先 `discover_pair_target` 自动拿到配对端口，再 `pair_then_connect`；填了 IP:端口 + 配对码 → 直接用填的；配对码**留空** → 直接 `connect_only`（已配对过时的兜底）。首次配对仍必须人工读手机上的 6 位码（协议限制）。 | 留空不是「输入不完整」，是「跳过配对」；IP:端口留空也不是错误 |

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