# 手机侧 MCP 服务（`ajpro-mcp-server.js`）

`node-proxy.py` 的**读/点能力**依赖手机上跑着一个 MCP 服务。它不在本仓库内（版权归其作者），
但接口很小、约定固定，任何满足下面契约的实现都可以替换。

## 来源与校验

本仓库实测所用的 `ajpro-mcp-server.js` 来自 **[https://autojspro.cn/](https://autojspro.cn/)** 的 AutoJs 资源合集
（夸克网盘分享目录 `autojs合集/脚本/`），**压缩包名：`ajpro-mcp-server最终版.zip`**。

| 校验项 | 值 |
|---|---|
| 解压后文件名 | `ajpro-mcp-server.js` |
| 大小 | 122,664 B |
| sha256 | `301e81f362fde8860d0220c6afd13c212d153610ca41c64daf04a12ebf35ae0a` |
| 内置版本标记 | 脚本头部注释写：`Auto.js Pro MCP 服务器（第二代引擎版）—— v7 终极版` |
| 运行环境 | Auto.js Pro 9.x 的 **Node.js 引擎**（内置 Node 16.14.0） |

> 同目录另有 `ajpro-mcp-server-rhino.zip`（`ajpro-mcp-server-rhino.js`）与 `ajpro-mcp-server5.0.zip`（`ajpro-mcp-server.js`）两个版本，
> **本仓库实测的是 `ajpro-mcp-server最终版.zip` 那份**；若换用其它版本，请对照下面的接口契约验证方法是否齐全。

## 放哪里 / 怎么跑

1. 把脚本放到手机：`/sdcard/脚本/ajpro-mcp-server.js`；
2. 在 **Auto.js Pro** 里打开并运行它（Node 引擎，AutoJsPro 9.x 自带 Node 16）；
3. 它会在 `0.0.0.0:6666` 起一个 HTTP JSON-RPC 服务（端口被占则顺延，`node-proxy.py` 会自动扫 6666–6675）；
4. 长期稳定运行建议：AutoJsPro →「前台服务」开启，并给 App 开 自启动=允许 / 省电=无限制；
5. 已在容器里的脚本可以这样远程拉起/停掉（无需碰手机）：

```bash
/root/ajrpc.py run /sdcard/脚本/ajpro-mcp-server.js   # 起
/root/node-proxy.py down                              # 停（engines.forceStop，保留 AutoJsPro 进程）
```

## 本工具需要的契约（最小集合）

HTTP POST，JSON-RPC 2.0（`Content-Type: application/json`）：

| 方法 | 用途 |
|---|---|
| `initialize` | 握手（`node-proxy.py` 探活时调用） |
| `tools/list` | 列出工具，`node-proxy.py ping` 用它判断在线并统计工具数 |
| `tools/call` → `run_code`，参数 `{"code": "<js>", "wait": <秒>}` | 在 AutoJsPro 引擎里执行 JS 并回结果（可选 `wait` 等待完成） |
| `tools/call` → `list_engines` | 列出脚本引擎（`node-proxy.py down` 用它找自身引擎并 `forceStop`） |

`run_code` 约定：

- 代码首行用 `"rhino";` 或 `"nodejs";` 指定引擎（本项目用的是 Rhino，一代引擎：`auto.*` / `click` / `press` / `images` 可用）；
- 结果通过**写文件**回传最稳：把 JSON 写到 `/sdcard/脚本/_mcp-out.json`（容器可直接读同一个路径）；
  本项目的 `node-proxy.py` 就是这样取结果的（每一步读的都是这个文件）。

## 没有这个脚本怎么办

- 如果你手上已有该文件（例如从原仓库或自己的设备 `/sdcard/脚本/` 里），直接拷到手机上即可；
- 若需要自行实现：按上表实现 4 个方法就能被 `node-proxy.py` 完整驱动；
- 只想"跑脚本"而不需要"读/点界面"时，可以**完全不装 MCP**——`ajrpc.py` 单独就能远程运行任意脚本。
