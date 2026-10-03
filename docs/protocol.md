# 协议细节（逆向所得，供二次开发）

本文件记录两条通道的**实测契约**。它们都不是公开 API，升级 AutoJsPro 后可能变化；本仓库的工具已按此实现，
如果哪天失效，从这里开始排查。

---

## 1. 调试 RPC（`ajrpc.py` 使用）

**形态**：手机侧 AutoJsPro 自己开的 WebSocket 服务（"智能连接"的设备端）。

- **端口动态**（"允许远程调试"打开时才监听；关掉即消失）。`ajrpc.py port` 扫 32768–65535 找它，
  并缓存到 `/root/.ajrpc-port`（连接失败会自动重扫）。
- **只认二进制帧**：文本帧会被直接丢弃。帧结构：

```
+----------------+------------------------------+
| int32 BE 类型  | 载荷                          |
+----------------+------------------------------+
   1 = JSON-RPC 2.0 文本     2 = 二进制数据块（36 字节 id + 数据）
```

- **每条 JSON 消息根部带 `token`**（客户端 UUID，客户端自己生成，存 `/root/.ajrpc-token`）。
- **连接后第一件事**：`debug.authorize`，参数 `{"token": "<uuid>"}`；手机弹 `DebugAuthConfirmActivity` 授权框，
  用户点「永久允许」后该 token 长期有效。成功返回：

```json
{"appVersion":"Pro 9.3.11-0","appVersionCode":9131100,"deviceId":"…","deviceName":"…","protocolVersion":1,"uuid":"…"}
```

**手机侧方法**

| 方法 | 参数 | 说明 |
|---|---|---|
| `debug.authorize` | `{token}` | 授权握手 |
| `debug.runFile` | **`{file}`**（手机绝对路径） | 运行脚本（写成 `path` 会回 `-32602`） |
| `debug.runProject` | 项目目录 | 运行项目 |
| `debug.stop` / `debug.stopAll` | — | 停止 |
| `debug.openTerminal` / `debug.updateTerminalSize` | — | 远程终端 |
| `vfs.readdir` / `vfs.readFile` / `vfs.writeFile` / `vfs.delete` / `vfs.rename` | 路径参数 | 文件系统 |
| `rsync.syncFiles` / `rsync.writeFile` / `rsync.cancelSyncFiles` | — | 文件同步（大文件分块） |

**手机 → PC 方向**（本仓库不实现，仅供理解）：`debug.clientLog`、`debug.debugEvent`、`rsync.getFile`、`rsync.syncCompletion`。

**官方客户端**：VSCode 插件 `hyb1996.auto-js-pro-ext`（它监听 **21029**，并用 mDNS 服务类型 `autojsprodebug`
发现设备）——上面这些名字与帧格式即从它的 JS 逆向而来。

---

## 2. MCP 服务（`node-proxy.py` 使用）

**形态**：手机侧 AutoJsPro 跑 `ajpro-mcp-server.js`（Node 引擎）→ HTTP JSON-RPC，默认 `0.0.0.0:6666`，
端口被占则顺延（工具扫 6666–6675）。合同见 [`../phone/README.md`](../phone/README.md)。

`node-proxy.py` 用到的方法：`initialize`、`tools/list`（探活）、`tools/call` →
`run_code`（参数 `code`、`wait`；结果由脚本写到 `/sdcard/脚本/_mcp-out.json` 后容器直读）、`list_engines`（找自身引擎以便优雅停止）。

### 2.1 为什么禁用、能从 AutoJsPro 读到微信的节点

微信按**无障碍服务类名**设白名单。AutoJsPro 的服务类名是
`com.google.android.accessibility.selecttospeak.SelectToSpeakService`（在白名单里），
DSHA 自带服务与 AutoX v7 的名字不在 → 读不到任何节点。

### 2.2 遍历与取值的坑（工具已处理，自写脚本时注意）

- 节点是**原生 AOSP `AccessibilityNodeInfo`**：`childCount` 是**属性**（不是方法）、子节点 `getChild(i)`、
  坐标必须 `getBoundsInScreen(new android.graphics.Rect())`（没有 `bounds()`，`boundsInScreen` 也不是可读属性）；
  文本是 **Java String**，比较前要 `String(t)`。
- **必须遍历 `auto.windows` 全部窗口**：只读 `rootInActiveWindow` 会被 AutoJsPro 自己的悬浮窗挡住
  （表现为"只有浮窗几个节点"）。
- 深度上限给到 **30**：微信聊天列表在第 17–25 层。
- `clk`（clickable）**不是入口判据**：小程序自绘 Tab 栏的图标与其容器可以**同时** `clk=false`。
  判据用结构：同一父节点下 **≥3 个等宽、上下对齐、横向互不重叠**的子节点 = 一行（`rows` 已实现）。
- **点击要真实触摸**：`press(x, y, ms)`（或 `gesture`）——小程序网页层（XWeb）不响应无障碍 `ACTION_CLICK`。
- **截图走无障碍**：`auto.service.takeScreenshot(displayId, executor, callback)` → `Bitmap.wrapHardwareBuffer`
  → `compress(PNG)` —— **零弹窗、无需媒体投影授权**。

---

## 3. 定位模型（`rows` 的方法论）

定位一个入口需要两个**互相不能推导**的因子：

| 因子 | 来源 | 说明 |
|---|---|---|
| 几何 | `rows`（等宽兄弟行）/ `dump --nodes` | 目标在哪（bounds、中心） |
| 语义 | 文字节点 / `ocr` 贴标 | 多个候选里哪个才是目标 |

候选唯一时可只用几何；**≥2 个候选必须补一次语义**（否则只能瞎猜第 k 格）。
`rows --ocr` 把两件事合成一条命令：行几何 + 逐格裁图 OCR → `#k 标签 @ (cx, cy)`。
