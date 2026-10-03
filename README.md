# dsha-autojs-bridge

**中文** | [English](README.en.md)

**让 DSHA（DeepSeek Harness Android，DSHA v0.1.7-rc2（正式版），https://github.com/DSH-APP/DSHA）用上 Auto.js Pro 的两项能力：**

| 工具 | 用途 | 通道 |
|---|---|---|
| **`node-proxy.py`**<br>`find` / `click` / `rows` / `dump` … | 在**微信、夸克、支付宝**这类"对无障碍服务设白名单"的应用里**读节点、点操作**。要点：<br>· `rows --ocr` 用**几何 × 语义**（等宽兄弟行 + 逐格 OCR 贴标）定位**没有文字的图标入口**<br>· **真实触摸注入**（`press`）点击——小程序网页层（XWeb）也收得到<br>· **无障碍截图**（`auto.service.takeScreenshot`）——**零弹窗、无需授权**<br>· 与 DSHA 自带 computer-use **同级**：命令一一对应（`dump/find/click/tap/swipe/key`） | AutoJsPro 的 **MCP HTTP 服务**<br>`127.0.0.1:6666`（占用则顺延） |
| **`ajrpc.py`**<br>`run` / `stopall` / `call` | 从容器**远程在手机上跑任意脚本 / 读写手机文件 / 停脚本** —— 即"手机上的 vibe coding"。<br>· 也是 `node-proxy.py ensure` 用来**拉起 MCP 服务**的底座（RPC 负责起服务，MCP 负责读/点）<br>· 任务结束用 `down`/`stopall` 关掉，AutoJsPro 进程与无障碍不受影响 | AutoJsPro 的**调试 RPC**<br>WebSocket、端口动态，**首次在手机上点一次「永久允许」** |

两者互补：**一个管"读/点界面"，一个管"执行脚本"**；都需要手机上装好 **Auto.js Pro 9.x**。

---

## 1. 解决什么问题

DSHA 自带 computer-use（`/app/ui/*` 读屏/点按）对**普通应用**很好用，但在下列场景会**必然失败**：

- 微信、夸克、支付宝等把无障碍服务**按服务类名白名单**的应用 → DSHA 的 `dump/tap/screenshot` 全部返回
  `你拒绝了这次屏幕读取 / 截屏 / 点击`，或挂到 60 秒超时；
- 小程序里**没有文字、`clickable=false`** 的图标入口（底部 Tab、宫格卡片）→ 直接读不到、点不动。

`app-node-proxy`（`node-proxy.py`）把这些场景补齐：

- 借 AutoJsPro 的无障碍（它的服务类名在白名单里）**遍历所有窗口**取节点 → 得到 `pkg / activity / 文本 / bounds / clickable`；
- `rows --ocr` 用**几何（等宽兄弟行）+ 语义（逐格 OCR 贴标）**一次定位图标入口；
- **真实触摸注入**（`press`）点击，网页层也收得到；
- **无障碍截图**（`auto.service.takeScreenshot`）——**零弹窗、无需授权**。

`autojspro-rpc`（`ajrpc.py`）解决另一类需求：**想让手机执行一段脚本**（本仓库的很多能力都靠它把 MCP 服务拉起来），
但不想每次手动去手机上点运行：

- `ajrpc.py run /sdcard/脚本/你的脚本.js` → 手机立刻执行；
- `ajrpc.py call vfs.readdir '{"path":"/sdcard/脚本"}'` → 读写手机文件；
- `ajrpc.py stopall` → 任务收尾停掉所有引擎。

---

## 2. 架构

```
┌────────────────────────── 手机 (Android) ──────────────────────────┐
│                                                                    │
│  DSHA App（proot Ubuntu 容器 = 本仓库工具运行处）                    │
│    node-proxy.py ──HTTP/JSON-RPC──┐                                 │
│    ajrpc.py      ──WebSocket──────┼──┐                              │
│                                   │  │                              │
│       ┌───────────────────────────┘  │                              │
│       ▼                              ▼                              │
│  Auto.js Pro（无障碍服务 + Node 引擎）                                │
│    ├─ MCP 服务 :6666  ← ajpro-mcp-server.js（手机侧脚本）             │
│    └─ 调试 RPC（动态端口，允许远程调试开启时才监听）                    │
│                                                                    │
│  目标应用（微信/夸克/…）←── AutoJsPro 的无障碍读节点 + 触摸注入         │
└────────────────────────────────────────────────────────────────────┘
```

容器与手机**共享网络栈**，所以容器里的 `127.0.0.1:6666` 就是手机本机端口。

---

## 3. 依赖与前置

| # | 依赖 | 说明 |
|---|---|---|
| 1 | **Android 手机 + DSHA App**<br>[DSH-APP/DSHA](https://github.com/DSH-APP/DSHA) **v0.1.7-rc2（正式版）**（实测版本） | 本工具跑在 DSHA 的容器里（proot Ubuntu，python3 ≥ 3.10） |
| 2 | **Auto.js Pro 9.x**（`org.autojs.autojspro`） | 手机侧执行者。**必须满足**：① 无障碍服务已启用；② 开发者调试 → **允许远程调试** 开启；③ 首次连接时在弹框点**「永久允许」**<br>实测版本 **9.3.11-0**（作者：[@Azek431](https://github.com/Azek431)） |
| 3 | **手机侧 MCP 服务脚本** `ajpro-mcp-server.js`<br>来源：[https://autojspro.cn/](https://autojspro.cn/)，**压缩包名 `ajpro-mcp-server最终版.zip`** | 放在手机 `/sdcard/脚本/ajpro-mcp-server.js`，在 AutoJsPro 里跑起来监听 `0.0.0.0:6666`。**本仓库不含该脚本**（版权归其作者），获取方式与接口契约见 [`phone/README.md`](phone/README.md) |
| 4 | Python 3.10+ | 容器内已有；OCR 功能需要 `rapidocr_onnxruntime`（可选，见下） |
| 5 | （可选）`rapidocr_onnxruntime` + `pillow` | 仅 `rows --ocr` / `ocr` 需要：`pip install rapidocr_onnxruntime pillow` |

> 为什么必须 Auto.js Pro：微信这类应用**只允许服务类名为白名单名字**的无障碍服务读取它（典型是
> `com.google.android.accessibility.selecttospeak.SelectToSpeakService`）。AutoJsPro 正好用这个名字，
> DSHA 自带服务与 AutoX v7 用的名字不在白名单 → 读不到。`node-proxy.py` 就是**借这条合法通道**工作。

---

## 4. 安装

### 方式 A：把这段提示词发给 DSHA 里的 agent（一键，推荐）

```text
请帮我安装并跑通 dsha-autojs-bridge（DSHA ↔ Auto.js Pro 桥）。步骤：

1. 克隆/解压本仓库到 /root/dsha-autojs-bridge（若已存在则 git pull）。
2. 运行 `bash /root/dsha-autojs-bridge/install.sh`，它会：
   - 把 tools/node-proxy.py、tools/ajrpc.py 装到 /root 并加执行权限；
   - 把 skills/app-node-proxy、skills/autojspro-rpc 装到 $DSH_HOME/skills/；
   - 打印环境自检（python 版本、MCP 是否在线、手机侧脚本是否就位）。
3. 按自检结果补齐前置，逐项告诉我需要我在手机上做什么（只说要点）：
   - Auto.js Pro → 无障碍服务开启、开发者调试 → 允许远程调试开启；
   - 手机侧 /sdcard/脚本/ajpro-mcp-server.js 是否存在，不存在则按 phone/README.md 指导我放好并运行；
4. 跑 `/root/ajrpc.py auth`：我手机上会弹授权框，我会点「永久允许」，你等我的回复。
5. 跑 `/root/node-proxy.py ensure` 起 MCP，再用 `/root/node-proxy.py ping` 确认在线。
6. 最后用一个只读测试验证：`/root/node-proxy.py dump --pkg com.tencent.mm`（微信在前台时）应返回几百个节点。
   把结果贴给我，别做任何点击。

注意：不要重装/重签名任何 App；不要用 adb/shell 绕过设备策略；只读操作可以直接做，任何点击先说明要点哪里。
```

### 方式 B：手动

```bash
git clone https://github.com/YoruJiQAQ/dsha-autojs-bridge.git
cd dsha-autojs-bridge
./install.sh            # 安装/升级（幂等）
./install.sh --check    # 只自检
./install.sh --uninstall
```

### 方式 C：作为 DSHA 插件（bundle）

本仓库符合 DSHA bundle 约定（`package.json` 的 `dsh.bundle.patch` + `cordis.patch.yml`）：

- 在 DSHA 的**插件页**安装本仓库（npm 包名或 git 地址均可），载入时会自动执行与 `install.sh` 等价的安装；
- 或者在 profile 的 `package.json` 里把本包加入 `dsh.profile.bundles`，重启后生效。

> 该 bundle 只做"安装工具与技能"一件事，不注册服务、不改动其它插件的行，可安全与任意 profile 组合。

---

## 4.5 在 DSHA 插件市场安装（符合 DSHA 插件规范）

本仓库是 **dsh 插件**（`package.json` 声明 `dsh.bundle.patch`，patch 与 `main` 均随包发布），可用市场支持的方式安装：

| 方式 | 做法 |
|---|---|
| **从链接安装（推荐）** | 在 [dsha.cc 插件市场](https://dsha.cc/) 或 App 内插件市场粘贴本仓库链接；**更推荐粘贴 [Releases](https://github.com/YoruJiQAQ/dsha-autojs-bridge/releases) 里 Release 附件的 HTTPS 直链**（固定版本、含已构建产物） |
| **导入插件包** | 下载 Release 附件 → App 插件市场 →「导入插件包」→ 用系统文件选择器选中 |
| **npm**（若已发布到 npm） | DSHA 终端执行 `dsha-plugin install <包名>` |
| **手动** | `git clone` 后 `./install.sh`（与插件载入时执行的动作等价） |

安装后请到**启动页重启 Web**，再回插件管理确认状态并验证功能。

### 加载验证记录（投稿要求项）

| 项 | 值 |
|---|---|
| DSHA 构建 | **v0.1.7-rc2（正式版）** |
| dsh 版本 | **0.1.7-rc.2** |
| 安装方式 | 仓库链接 / 插件包导入 / `install.sh` |
| 加载验证 | 载入 `lib/index.js` → 安装 2 个工具（`/root/node-proxy.py`、`/root/ajrpc.py`）+ 2 个技能（`$DSH_HOME/skills/app-node-proxy`、`autojspro-rpc`），日志输出 `[dsha-autojs-bridge] 已就位 4 项…`；`install.sh --check` 全项通过 |
| 功能验证 | 微信前台：`node-proxy.py dump --pkg com.tencent.mm` → 600+ 节点；`rows --ocr` → 图标行逐格标签；`node-proxy.py ensure` → `ajrpc.py` 远程拉起 MCP 成功；`ajrpc.py run <脚本>` 在手机执行成功 |

### 依赖、数据与权限（投稿要求项）

- **依赖**：手机侧 Auto.js Pro 9.x（无障碍服务 + 允许远程调试）与 `ajpro-mcp-server.js`；容器侧 Python ≥3.10（OCR 可选装 `rapidocr_onnxruntime`+`pillow`）。**无原生依赖**，纯 JS 插件 + Python 脚本。
- **数据与隐私**：两个工具**只与本机 `127.0.0.1` 通信**（AutoJsPro 的 MCP / 调试服务），**不联网、不上传任何截图或文件、不外发到任何外部服务**。
- **权限**：容器内写入 `/root`（两个脚本）与 `$DSH_HOME/skills`（两个技能）；手机侧需要无障碍与远程调试授权。
- **不含**任何 API Key / token / 真实私人数据；技能与文档中的界面文案均为公开示例。


## 5. 手机侧一次性设置

1. **Auto.js Pro**：无障碍服务 **开启**（授权时选"始终允许"）；
2. **Auto.js Pro → 开发者调试 → 允许远程调试 = 开**（`ajrpc.py` 依赖它）；
3. **手机侧 MCP 脚本**：把 `ajpro-mcp-server.js` 放到 `/sdcard/脚本/`，在 AutoJsPro 里运行一次（监听 6666）；
   长期可用则可给它开「前台服务」+ 自启动/省电无限制；
4. **首次授权**：`/root/ajrpc.py auth` → 手机弹框点 **「永久允许」**（token 存容器 `/root/.ajrpc-token`，之后不再弹）。

---

## 6. 快速开始

### 6.1 在微信里读界面 / 点操作（node-proxy）

```bash
/root/node-proxy.py route com.tencent.mm      # 先问该走哪条路（受限应用会答：直接走 MCP）
/root/node-proxy.py ensure                    # 进入状态（2.6s 拉起 MCP，幂等）
/root/node-proxy.py dump --pkg com.tencent.mm # 节点快照（pkg/act/文本/bounds/clickable）
/root/node-proxy.py find 通讯录 --pkg com.tencent.mm
/root/node-proxy.py click 通讯录 --pkg com.tencent.mm --verify 朋友圈   # 带候选选择 + 点后回执校验
/root/node-proxy.py down                      # 收尾必做
```

命令一览：

| 命令 | 用途 |
|---|---|
| `ensure` / `ping` / `down` | 起（幂等）/ 探活 / 收尾关闭 MCP |
| `route <包名>` | 判定该走 DSHA 桥还是 MCP |
| `dump [--pkg] [--nodes] [--clickable] [--rect x1,y1,x2,y2] [--min-y N] [--all] [--json]` | 节点快照 |
| `find <词> [--pkg] [--all]` | 找 text/desc 命中的节点（带 `on`/`clk`/`anc`/bounds/中心） |
| `click <词> [--pkg] [--index N] [--verify 词]` | 点节点（候选过滤 + 点后回执校验 + 6s 重试窗） |
| `rows [--pkg] [--band y1 y2] [--ocr]` | **等宽兄弟行 → 每格标签与中心**（图标行/Tab/宫格） |
| `ocr [--pkg] [--rect x1,y1,x2,y2] [--y N] [--full]` | 无障碍截图 + 局部轻量 OCR |
| `tap <x> <y> [--press-ms 60] [--verify 词]` | **真实触摸**点按（网页层也生效） |
| `swipe x1 y1 x2 y2 [--ms]` / `key back\|home\|recents` | 滑动 / 按键 |
| `wait --until "kw:<词>\|act:<名>\|nodes>N\|stable" [--timeout]` | **就绪判据**（替代裸 sleep） |
| `hotspot x1 y1 x2 y2 [--step N]` | 网格试探真实热区（兜底） |
| `shot [--out 路径]` | 无障碍截图（无弹窗） |

### 6.2 在手机上跑脚本（ajrpc）

```bash
/root/ajrpc.py port                            # 找调试端口（缓存 /root/.ajrpc-port）
/root/ajrpc.py auth                            # 首次授权（手机点「永久允许」）
/root/ajrpc.py run /sdcard/脚本/hello.js       # 运行脚本
/root/ajrpc.py stopall                         # 停掉所有引擎
/root/ajrpc.py call vfs.readdir '{"path":"/sdcard/脚本"}'
/root/ajrpc.py listen                          # 打印收到的每一帧（排查用）
```

> `node-proxy.py ensure` 内部就是 `/root/ajrpc.py run /sdcard/脚本/ajpro-mcp-server.js`：
> **RPC 负责把 MCP 拉起来，MCP 负责读/点**——两者配合即完成"全自动开关"。

---

## 7. 随包技能（agent 会自动使用）

安装后，DSHA 里新建会话的 agent 会在合适场景自动加载：

| 技能 | 何时用 |
|---|---|
| `app-node-proxy` | 在微信/夸克/支付宝等受限应用里读界面、点操作（含图标入口、点击纪律、重叠不变量、故障排查） |
| `autojspro-rpc` | 需要在手机上跑脚本、读写手机文件、停脚本时（含协议、排障、安全边界） |

技能里写了大量**实测契约**（例如：微信里不要先试 DSHA 桥；点击要用真实触摸；`--verify` 有 6 秒重试窗；
图标入口用 `rows --ocr` 而不是猜坐标），这些能显著减少 agent 的无效尝试。

---

## 8. 目录结构

```
dsha-autojs-bridge/
├── README.md                      # 中文文档
├── README.en.md                   # 英文文档
├── LICENSE                        # MIT
├── .gitignore
├── install.sh                     # 幂等安装/自检/卸载
├── package.json                   # DSHA bundle 元数据（dsh.bundle.patch）
├── cordis.patch.yml               # bundle patch：载入本插件
├── lib/index.js                   # 插件：安装工具与技能（幂等、失败只告警）
├── tools/
│   ├── node-proxy.py              # 受限应用的读/点（走 MCP）
│   └── ajrpc.py                   # 远程跑脚本 / 文件读写（走调试 RPC）
├── skills/
│   ├── app-node-proxy/SKILL.md
│   └── autojspro-rpc/SKILL.md
├── phone/README.md                # 手机侧 MCP 脚本：获取方式与接口契约
└── docs/
    ├── protocol.md                # 两条通道的协议细节（逆向所得）
    └── agent-install-prompt.md    # 一键安装提示词（同一段，便于复制）
```

---

## 9. 排障速查

| 症状 | 处理 |
|---|---|
| `ensure` 说 RPC 不可用 | AutoJsPro 的「允许远程调试」没开（开关一次；端口会变，工具会自动重扫） |
| 手机弹授权框后没反应 / `user rejected` | 重跑 `/root/ajrpc.py auth`，在手机上点**「永久允许」** |
| `-32602 method parameters invalid` | `debug.runFile` 的参数名是 **`{"file": …}`**（不是 `path`） |
| MCP 不在线 | 手机上没有运行 `ajpro-mcp-server.js`（或 AutoJsPro 被杀）→ 手动跑一次，或 `ensure` 重试 |
| 微信里 `dump` 返回"你拒绝了这次屏幕读取" | 说明你在用 DSHA 桥——**微信里必然如此**，改用 `node-proxy.py` |
| 切到别的应用后请求挂住 | AutoJsPro 被系统冻结 → 开「前台服务」+ 自启动/省电无限制 |
| `rows --ocr` 报缺依赖 | `pip install rapidocr_onnxruntime pillow` |

---

## 10. 安全边界

- 本工具组合具备"**在任意应用里点击任意坐标 / 在手机上执行任意脚本**"的能力：
  涉及**支付、转账、发消息、删数据、改设置、退出登录**的操作**必须先由用户确认**；
- 只读操作（`dump` / `find` / `rows` / `shot` / `vfs.readdir`）可直接做；任何点击与写操作都应说明"点了什么、写了什么"；
- **不要**用它绕过设备策略（DSHA 的 `[POLICY_BLOCKED]` 即"不能做"，换通道也不行）；
- 不要把手机隐私内容（token、日志原文、聊天记录）外传；
- 请遵守目标应用的服务条款与当地法律；本项目仅提供技术通道，使用者自负责任。

## 11. License

MIT（见 [LICENSE](LICENSE)）。手机侧 `ajpro-mcp-server.js` 不在本仓库内，版权归其作者。
