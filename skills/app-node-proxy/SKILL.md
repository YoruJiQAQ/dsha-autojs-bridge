---
name: app-node-proxy
description: 在微信、夸克、支付宝等"对无障碍服务设白名单"的应用里读界面/点操作时加载。**这类应用直接走 MCP 节点代理，不要试 DSHA 桥**：/root/node-proxy.py ensure → dump/find/click/swipe/key → down。它和 DSHA computer-use 是同级工具（命令一一对应），微信实测可读 500~600+ 节点含坐标、可直接点击；**图标/图像类入口**（无文字、clk=false）用 `rows --ocr` 一次拿到「几何 + 每格标签」再 `tap` 中心（定位=几何×语义双因子）。截图走 AutoJsPro 无障碍截图，**无需人工授权**。含常见故障与安全边界。
---

# 受限应用里的界面操作（MCP 节点代理 = 与 DSHA 同级的一等工具）

## 0. 一句话流程

```bash
/root/node-proxy.py route com.tencent.mm    # 可选：确认该走哪条路（受限应用会直接告诉你用 MCP）
/root/node-proxy.py ensure                  # 进入状态（幂等：在线就秒回，不在线 2.6s 拉起来）
#   dump / find / click / tap / swipe / key   ← 和 DSHA 的 dump/tap/swipe/key 一一对应，照常用
#   rows --ocr                                ← 图标行/Tab 栏/宫格：一次拿到「几何 + 每格标签」
/root/node-proxy.py down                    # 收尾必做（2s）
```

**规则（按优先级）**：

1. **目标是受限应用（微信 `com.tencent.mm`、夸克、支付宝、淘宝…）→ 完全不碰 DSHA 桥，直接 `ensure` 走 MCP**。
   不要"先试一下 DSHA"——在微信里它必然挂到 60 秒超时才返回"你拒绝了这次…"，纯浪费轮次。
2. **目标是普通应用 → 用 DSHA 桥**（`/app/ui/*`，轻量、无需起 MCP）；只有它读不到时才 `ensure` 切 MCP。
   判断"读不到"要**客户端 5 秒超时**（`curl -m 5`），不要等桥自己超时。
3. **截图走无障碍，无需授权**：`shot`/`ocr` 用 `auto.service.takeScreenshot`（AutoJsPro 的服务类名在白名单里，**零弹窗**）。
   需要"知道某个图标/卡片是什么"时就用 `ocr`（截屏 + 按 bounds 裁区域 + 容器内 RapidOCR），不用整屏 OCR、更不用请用户截图。
4. DSHA 的 `/app/launch`、`/app/device`、`/app/apps`、`/app/clip` 等**不受影响**，照常用。
5. 收尾 `down`（否则 MCP 常驻占资源）。

## 0.5 开局纪律（照抄，省掉最贵的一次浪费）

任务落到"微信/夸克/支付宝等受限应用"时，**第一条命令就是它，不要先做别的**：

```bash
/root/node-proxy.py ensure          # 2.6s 进入状态（幂等，在线秒回）
```

- ❌ **绝对不要先跑 `/app/ui/dump`（或 screenshot/tap）来"确认一下"**。在微信里它 **100%** 会挂到 60 秒超时后才回
  `[ERR] 你拒绝了这次屏幕读取` / `你拒绝了这次点击`。**那是已知结论，不是需要现场验证的假设**；
  看到这个报错 = 你走错路了，立刻改 `ensure` 走 MCP，**不要重试第二次**。
- ✅ `/app/launch`、`/app/device`、`/app/apps`、`/app/clip` 这些**接口层**在微信里正常，照用（启动应用就走 `/app/launch`）。
- ✅ 已知受限名单见 `route <包名>`；拿不准时先 `route`，一条命令就有答案。

## 0.6 流畅度：把连续动作合成一次调用

同一个应用的连续步骤**尽量一条 bash 串起来**（每步都会返回回执，信息不会丢），减少往返：

```bash
# 例：进小程序后，用两轮完成"定位 + 动作"（不要探索式多轮）
/root/node-proxy.py swipe <上点x> <y1> <同x> <y2> --ms 500   # 从列表顶部往下拖（y1≈屏高1/3, y2≈屏高5/6） \
 && sleep 1 && /root/node-proxy.py click 孵蛋工具箱 --pkg com.tencent.mm --verify 孵蛋结果查询 \
 && sleep 4 && /root/node-proxy.py dump --pkg com.tencent.mm --nodes \
 && /root/node-proxy.py ocr --pkg com.tencent.mm --y 2200 \
 && /root/node-proxy.py tap <该格中心x> <该格中心y>
```

- 复查**只需要**落在关键跳转上（页面/activity 切换、进小程序、点击图标入口）；普通滚动、输入不用逐步复查；
- 进入小程序/切换页面后**先 `sleep 3~5`**（节点数会先从 ~100 涨到稳定值），否则 dump 到的是半成品；
- 收尾 `down` 和主任务串在一条命令里跑，别单独占一轮。

## 1. 与 DSHA computer-use 的命令对照（当同级工具用，别当成"另一套流程"）

| 动作 | DSHA（普通应用） | MCP 节点代理（受限应用） |
|---|---|---|
| 读屏 | `curl /app/ui/dump` | `node-proxy.py dump [--pkg …] [--clickable] [--all]` |
| 找文字 | dump 后自己找 | `node-proxy.py find <词> [--pkg …] [--all]` |
| 按文字点 | `/app/ui/tap?text=<词>` | `node-proxy.py click <词> [--pkg …] [--index N] [--verify <下一页特征词>]` |
| 按坐标点 | `/app/ui/tap?x=&y=` | `node-proxy.py tap <x> <y>` |
| 滑动 | `/app/ui/swipe?…` | `node-proxy.py swipe x1 y1 x2 y2 [--ms 400]` |
| 按键 | `/app/ui/key?name=back` | `node-proxy.py key back` |
| 截屏 | `/app/ui/screenshot` | `node-proxy.py shot`（**无障碍截图，无需授权**） |
| 看图标是什么 | — | `node-proxy.py ocr [--pkg …] [--y 2200] [--full]`（无障碍截图 + 按容器 bounds 裁区域 + 轻量 OCR 贴标签） |
| 枚举无文字节点 | — | `node-proxy.py dump --nodes [--pkg …] [--rect x1,y1,x2,y2] [--min-y N]`（含 `Image`/`View`，带 depth/cls/bounds） |
| **图标行/网格定位** | — | `node-proxy.py rows [--pkg …] [--band y1 y2] [--ocr]`（等宽兄弟行 → `#k 标签 @ (cx,cy)`） |
| 启动应用 | `/app/launch?pkg=` | 仍用 DSHA（该接口不受限） |
| 热区扫描 | — | `node-proxy.py hotspot x1 y1 x2 y2 [--step 60] [--verify 词]`（网格轻点，命中即停，用于"坐标对不上"） |
| **等就绪** | — | `node-proxy.py wait --until "kw:<特征词>"｜"act:<名>"｜"nodes>N"｜"stable" [--timeout 12]`（**替代裸 sleep**） |
| 起 / 停代理 | — | `ensure` / `down` |

**输出格式（重要，别踩）**：CLI 的 stdout 是**人类可读摘要**（`dump` 前 30 条、`find` 前 20 条），**不是 JSON**；
完整结构化数据每次都写在 **`/tmp/node-proxy-last.json`**（下次调用覆盖）。三种取全量方式：

```bash
/root/node-proxy.py dump --pkg com.tencent.mm --json          # 直接打全量 JSON
/root/node-proxy.py find 跳过 --json | python3 -m json.tool    # 管道处理
# 或者每步都落盘，事后分析：
python3 -c "import json;d=json.load(open('/tmp/node-proxy-last.json'));[print(x['t'],x['b'],x['clk']) for x in d['texts']]"
```

字段：`dump` → `windows[] + total + offscreen(丢弃数) + texts[{t,d,cls,id,b,clk,on,icon}]`；
`find` → `hits[{src,t,d,cls,on,clk,anc,b,c}]`；`click` → `candidates[] + pick + hit + clicked + verify + warning`。

## 2. 标准打法（受限应用里）

1. `ensure` → `dump --pkg <包名>`（或直接 `find <目标文字>`）；
2. `click <文字> --verify <下一页特征词>`；要精确落点用 `tap x y`；
3. **必须复查**（见下）；4. 滚动 `swipe`、返回 `key back`。

### 2.1 点击的三条纪律（血泪教训）

- **看候选表再点**：`click` 会打印候选（`on`=是否在屏内、`clk`/`anc`=可否点）并自动挑"在屏内 + 可点优先"的那个。
  同名文本多命中时（例：`微信` 既是标题又是底部 Tab），**用 `--index N` 明确指定**，别赌自动挑对。
  `--index` 是**可用池**里的序号（池 = 在屏内候选，池空才退到离屏候选）；候选表里选中的那条用 **★** 标出，
  所以"先看表、再按 ★ 复核"就能确认到底点了谁；
- **必须给 `--verify` 或事后自己复查**：微信底部 Tab 切换**不改 activity**（都是 `LauncherUI`），
  所以 `click` 回执里的 `before/after act` **不能**当成功判据；可靠信号是 **① 节点数变化 ② 目标页特征词出现**。
  `click` 已经把这两项做进回执（`verify.nodesBefore/After/changed`、`verify.keywordFound`）；
- **点了没变化会显式告警**：`⚠️ 点击后页面无变化…` = 要么点空（候选在离屏副本上），要么本来就在该页（幂等）。
  看到告警**不要往下推进任务**，先 `find` 确认自己在哪一页。

### 2.2 页面基线（微信，实测节点数 —— 用来判断"我在哪一页"）

| 页面 | activity | 节点数 | 特征词 |
|---|---|---|---|
| 聊天列表 | `ui.LauncherUI` | 随数据量变化（**别记数字，用特征词**） | `微信(223)`、聊天名（陈思洁…） |
| 发现页 | `ui.LauncherUI` | 同 activity（**必须靠特征词区分**） | `朋友圈 b=[151,296,289,358]`、`扫一扫`、`听一听` |
| 朋友圈 | `ImproveSnsTimelineUI` | 随内容丰富度变化 | — |
| 小程序面板（下拉后） | `ui.LauncherUI` | 比聊天列表明显减少 | `孵蛋工具箱` 的 b 变成正值 |
| 某小程序 | `AppBrandUI00` | ~153（点击后瞬时 100，**要 sleep 3~5s 再 dump**） | `孵蛋结果查询` 等页内文本 |

### 2.3 配方：下拉引出小程序面板（反馈 §5）

前提：**必须先在微信 Tab（聊天列表）**——发现页也有个叫「小程序」的列表项，极具误导性；
起手点要**避开顶部横幅**。实测参数：

```bash
/root/node-proxy.py swipe <上点x> <y1> <同x> <y2> --ms 500   # 从列表顶部往下拖（y1≈屏高1/3, y2≈屏高5/6）      # 有效
# 成功判据：节点数掉到 ~480，且 find 孵蛋工具箱 的 b 是正值（不是 [0,0,-1901,-1325]）
```

**实测基线（微信，2026-10-03）**：`dump --pkg com.tencent.mm` → **501 节点 / 92 条文本**（含小程序列表：孵蛋工具箱、小猪胖胖加电站、向末日开兑换码…）；
`find 通讯录` → `b=[357,2340,453,2384]`；`click 通讯录` → `ok:true`；复查节点 **501→618** 且出现「新的朋友」「标签」= 页面确实切换。
**全程不需要截图。**

## 2.4 入口定位模型：**几何 × 语义**（两个因子，缺一不可）

定位一个可点入口需要两个独立因子，**任何一个都不能推出另一个**：

| 因子 | 回答的问题 | 可靠来源 | 失效情形 |
|---|---|---|---|
| **几何** | 目标在屏幕哪里（bounds/中心） | `rows`（等宽兄弟行）或 `dump --nodes` | 无 |
| **语义** | **多个候选里哪个才是目标** | 文字/desc 节点，或 `ocr` 贴的标签 | 标签画在图像里 → 文字节点为空 |

**推论（关键）**：

- 候选**唯一**时可以只靠几何；**≥2 个候选必须配一次语义**——否则只能瞎猜第几格。
- **图片式入口（图标/Tab/宫格）恰恰是"有几何、没语义"**：`find` 命中 0 是正常的（标签在画里），
  `dump --clickable` 里也没有它（`clk=false`）。这不是"没有入口"。
- ✅ **首选一条命令拿全两个因子**（几何 + 逐格标签）：

```bash
/root/node-proxy.py rows [--pkg <包名>] [--band <y1> <y2>] --ocr
# 输出形如：#3 『<标签>』 @ (cx,cy)  b=[l,t,r,b]     ← 直接拿去 tap
```

- 只有 `rows` 也分不出来时（格子内是纯图形符号、OCR 无字），才用 `hotspot` 网格试探兜底。

### 2.4.1 "行"的判据（不依赖 clk）

> **同一父节点下出现的"行"**：子节点 **≥3 个**、**宽度一致**、**上下边界对齐**、且**横向互不重叠**（排除嵌套/堆叠节点）。

`rows` 已内置该判据。**不要**用 `clickable` 判断入口——实测小程序自绘 Tab 栏里**图标与容器可以同时 `clk=false`**，
按"找 `clk=true` 父容器"的思路会找不到而误判"本页没有入口"。

### 2.4.2 动作：几何给落点，语义给选择

```bash
/root/node-proxy.py tap <第k格中心x> <中心y> --press-ms 60 --verify "<该格标签或目标页特征词>"
```

- 图标/网页绘制元素**必须用真实触摸**（`press`；`click`/a11y 对网页层无效）；
- `--verify` 现在自带 **6 秒重试窗**（渲染竞态不会假报失败）；判定只认它，**不认瞬时节点 delta**。

## 2.5 时间与调用契约（系统工程视角）

- **两个量分开对待**：
  - **稳定量**（页面的结构、图标行的相对排布）→ 用 `dump --nodes` **当场取一次**即可复用整段任务，不要每步重取；
  - **状态量**（当前在第几页、加载完没）→ 用**就绪判据**判断，不用固定 `sleep`。
- **就绪判据（readiness predicate）**：进小程序/切页后，等到"**节点数稳定** + **目标页特征词出现**"再动作；
  连续两次 `dump` 的节点数相同 = 稳定。**没有判据就不要用长 sleep**。
- **回执三态**：`成功 / 未判定（页面加载中）/ 失败`。渲染过渡期里 `changed=False` ≠ 点空——
  **先等一个就绪周期再判定**，不要立刻重复点击（重复点击可能把页面点乱）。
- **节点数是状态量，不是判据**：切页瞬间的节点数**可能暴涨也可能骤降**，与稳定值无关；
  只用 `wait --until "kw:<特征词>"`（或 `nodes>N` 且连续两次相同）判定，**忽略回执里的瞬时 delta**。
- **重叠不变量（动作前强制检查）**：目标 bounds 与其它可点元素重叠时，**落点可能被判给上层元素**——
  典型是小程序 Tab 栏 overlay 盖住其下的按钮：按文字点那个按钮会回 `ok:true`、`changed=True`，但真实落点是 Tab 栏
  （**回执看不出来**）。做法：取目标 bounds 与同屏其它可点 bounds 求交，非空时选更安全的落点或先滚动让目标独占一条带。
- **幂等优先**：能重复执行且无害的动作（`ensure`、`key back`、`dump`）可以放心重试；
  有副作用的（发消息、提交、支付）**必须先问**。

## 3. 踩过的坑（工具已处理；自己写脚本时注意）

- ❌ 只遍历 `rootInActiveWindow` → AutoJsPro **自己的悬浮窗在最上层**会挡住它（表现为"只有浮窗几个节点"）→ 必须遍历 **`auto.windows`** 全部窗口；
- ❌ 深度上限太小 → 微信聊天列表在**第 17~25 层**，上限给 **30**；
- ❌ 节点是**原生 AOSP 对象**：`childCount` 是**属性**（不是方法）、子节点 `getChild(i)`、坐标必须 `getBoundsInScreen(new android.graphics.Rect())`
  （没有 `bounds()`，`boundsInScreen` 也不是可读属性）；
- ❌ 文本是 **Java String** → 比较前 `String(t)`（否则报 `indexOf is not a function`）；
- ⚠️ **可视判定要用通用公式**（工具已内置）：`left < 屏宽 && right > 0 && top < 屏高 && bottom > 0 && right > left && bottom > top`。
  离屏节点有两种形态：**负值**（`b=[0,155,-324,217]`）和**超出屏宽的正值**（微信 ViewPager 相邻页被平移，如 `朋友圈 b=[2311,296,1080,358]`）；
  隐藏的小程序面板也会常驻节点树（`b=[0,0,-1901,-1325]`）；
- ⚠️ **`find` 命中 ≠ 可见可点**：隐藏面板里的节点照样会被命中 → **每次 find 之后都要看 `on`/`b` 是否在屏内**（工具默认已丢弃离屏命中，并回报"离屏丢弃 N 条"）；
- ⚠️ **区域参数不要"按文字节点猜"**：`ocr --y <像素>` 是按该区域内的**文字节点**裁条带的——
  当底部条带里有别的大按钮文字时，它会裁到那个按钮、输出垃圾。**正确做法：先 `dump --nodes` 看清要识别的那一行
  由哪些节点构成（通常是 `Image`），按"图标行"的 bounds 裁条带**，或直接指定矩形范围。
- ⚠️ 非目标窗口的残留内容（文件预览等）会带垃圾坐标混进来 → 默认已被"在屏内"过滤丢掉，需要时用 `--all` 才看得到。
- 🧩 **图标/图片类入口**：`dump` 默认只收"有文字/有 desc"的节点 → 图标**根本不会出现**；`find` 也会命中 0。
  **不要用 `clk` 找入口**：实测小程序自绘 Tab 栏里**图标与其父容器可以同时 `clk=false`**，
  "找 `clk=true` 父容器 → 找不到 → 判定本页没有入口"是**错误推理**。
  正解：按 **§2.4 的双因子模型**——`rows --ocr` 一次拿"几何 + 语义"（`#k 标签 @ (cx,cy)`），直接 `tap` 那一格中心。
  需要交叉核对时可用 AutoJsPro 的布局分析悬浮窗（长按控件 →「查看控件信息」看 `className/bounds/clickable/depth`）。
- 🚫 **小程序里"网页渲染"的元素可能吃不到点击**（典型：并排卡片/宫格入口）。判据：`find` 显示 `clk=false && anc=false`，
  且 `tap` 回执 `changed=false`。**判据**：同一页里**原生控件**（输入框）触摸注入有效（点原生输入框 → 节点数明显增加、软键盘弹出），
  而对卡片区做 **16 点网格扫描**（`hotspot 120 1840 960 2040 --step 120`）**全部无响应** → 说明合成触摸进不到 XWeb 的网页层（真手指可以）。

## 4. 常见故障

| 症状 | 处理 |
|---|---|
| `ensure` 报 RPC 不可用 | AutoJsPro「开发者调试 → 允许远程调试」没开（开关一次；端口会变，工具自动重扫） |
| `user rejected` | 授权框被拒/超时 → `/root/ajrpc.py auth` 重发，**请用户点「永久允许」** |
| `-32602 method parameters invalid` | `debug.runFile` 的参数名是 **`{"file": …}`**（不是 `path`） |
| 切到别的应用后请求挂住 | AutoJsPro 被系统冻结 → 请用户开「前台服务」+ 自启动/省电无限制 |
| `dump` 只有零星节点 | 确认遍历了 `auto.windows`（工具已做）；仍少说明该页面确实无节点（游戏/Canvas/Flutter） |
| 需要"看画面" | 才用 `shot`（要用户手动同意一次），否则别用 |
| `find` 命中 0 / `dump --clickable` 里没有该入口 | 正常：图标类入口无文字且 `clk=false` → 走 **§2.4**：`rows --ocr` 拿 `#k 标签 @ (cx,cy)` → `tap` 该格中心（真实触摸） |
| `tap` 回执 `changed=True` 但界面没切 | 疑似**重叠命中**（上层 overlay 盖住了下层按钮，见 §2.5 重叠不变量）→ 换安全落点或先滚动 |
| 需要"看画面" | `shot`/`ocr` 现在**无需授权**（AutoJsPro 无障碍截图），可直接用 |

## 5. 底层能力（需要跑脚本 / 读文件时）

```bash
/root/ajrpc.py port|auth|call <method> <json>|run <手机脚本路径>|stopall|listen
# 例：/root/ajrpc.py run /sdcard/脚本/xxx.js        ← 远程在手机上跑脚本
#     /root/ajrpc.py call vfs.readdir '{"path":"/sdcard/脚本"}'
```

协议要点（源自官方 VSCode 插件 `hyb1996.auto-js-pro-ext` 逆向）：WebSocket **只认二进制帧**；
帧 = 4 字节大端 int32 类型（1 = JSON-RPC 2.0 / 2 = 二进制块）+ 载荷；每条 JSON 根部带 `token`；
连接后先 `debug.authorize`（手机弹授权框，点「永久允许」后长期有效）。

## 6. 安全边界

- 代理能"点任何节点/坐标"：**支付、转账、发消息、删数据、改设置、退出登录**前**必须先问用户**；
- 只读（`dump` / `find`）可直接做；任何点击都要在回复里写清"点了什么、在哪"；
- 隐私内容只在用户明确要求的范围内使用；**不要把 token、日志原文外传**；
- **禁止重签名/重装 DSHA**（等于清空容器数据）。

## 9. 输出风格（默认面向结果）

- **默认只讲结果**：做了什么、看到什么、结论是什么、下一步需要用户做什么。**不要**把命令清单、调试过程、
  "这套链路暴露的问题/踩坑复盘"写进默认回复——用户要的是任务结果，不是工具的内部报告。
- 只有用户明确要求"**测试** / 复盘 / 反馈问题 / 排查"时，才输出：命令与回执明细、基线对照、发现的缺陷与建议。
- 汇报任务结果时给**关键证据**（页面切换前后的特征词/节点数、OCR 到的文本）即可，不必贴完整 JSON；
  完整数据本来就在 `/tmp/node-proxy-last.json`，用户要时再给路径。
- 需要用户动手时（授权、点一次、二选一），**用一句话把动作说清楚**，不要夹在一堆技术细节里。
