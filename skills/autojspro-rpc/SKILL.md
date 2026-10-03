---
name: autojspro-rpc
description: 需要在手机上运行/停止任意 Auto.js Pro 脚本、或读写手机文件时加载（DSHA 侧远程驱动 AutoJsPro，即"手机上的 vibe coding"）。用法：/root/ajrpc.py run /sdcard/脚本/xxx.js、/root/ajrpc.py port|auth|call <method> <json>|stopall|listen。走 AutoJsPro 自带的调试 RPC（二进制帧 + JSON-RPC 2.0 + token，端口动态、首次需在手机上点一次「永久允许」）。与 app-node-proxy 同源：那个是"读/点界面"，这个是"跑脚本/读写文件"。含协议、排障与安全边界。
---

# 远程驱动 Auto.js Pro（手机脚本 / 文件读写）

## 0. 一句话

```bash
/root/ajrpc.py run /sdcard/脚本/xxx.js     # 在手机上跑任意脚本（无需 UI、无需人工点击）
/root/ajrpc.py stopall                     # 停掉手机侧所有脚本引擎
/root/ajrpc.py call vfs.readdir '{"path":"/sdcard/脚本"}'   # 读写手机文件
```

这就是把 AutoJsPro 当"手机端的执行后端"：agent 在 DSHA 容器里写脚本 → 远程投送到手机运行 → 结果回读。

## 1. 与 app-node-proxy 的分工

| 需求 | 用哪个 | 通道 |
|---|---|---|
| 在受限应用（微信等）里**读/点界面** | `node-proxy.py` | AutoJsPro 的 **MCP HTTP 服务**（6666） |
| 在手机上**跑脚本 / 读写文件 / 停脚本** | `ajrpc.py` | AutoJsPro 的**调试 RPC**（WebSocket，端口动态） |
| 需要"节点能力"的脚本（`auto.click` 等） | 先用 `ajrpc.py run` 起 MCP，再用 `node-proxy.py` | 两者组合 |

两者都依赖 **Auto.js Pro 已安装 + 无障碍服务已启用**。

## 2. 前置（一次性）

1. AutoJsPro → **开发者调试 → 允许远程调试** = 开启（开着才有调试服务在监听；端口每次可能不同，工具会自动扫）；
2. 首次调用 `ajrpc.py auth` 时，手机上会弹授权框 → 点 **「永久允许」**；
   token 存在容器 `/root/.ajrpc-token`，之后不再弹（AutoJsPro 重装/清数据才需重来）。
3. 若报 `user rejected` → 重新 `auth`，请用户点允许（别反复重试）。

## 3. 命令

```bash
/root/ajrpc.py port      # 找调试服务端口（结果缓存 /root/.ajrpc-port，端口变了会自动重扫）
/root/ajrpc.py auth      # 首次授权
/root/ajrpc.py run <手机上的绝对路径>        # 运行脚本（debug.runFile）
/root/ajrpc.py stopall   # 停掉所有脚本引擎（debug.stopAll）
/root/ajrpc.py call <method> '<json>'       # 通用调用
/root/ajrpc.py listen    # 只监听并打印收到的每一帧（排查用）
```

可用的手机侧方法：

| 方法 | 参数 | 用途 |
|---|---|---|
| `debug.authorize` | `{"token": "<uuid>"}` | 握手授权（`auth` 已封装） |
| `debug.runFile` | **`{"file": "<手机绝对路径>"}`** | 运行脚本文件（注意参数名是 `file`，写成 `path` 会回 `-32602`） |
| `debug.runProject` | 项目目录 | 运行项目 |
| `debug.stop` / `debug.stopAll` | — | 停单个 / 全部 |
| `vfs.readdir` / `vfs.readFile` / `vfs.writeFile` / `vfs.delete` / `vfs.rename` | 路径等 | 手机文件系统操作 |
| `rsync.syncFiles` / `rsync.writeFile` / `rsync.cancelSyncFiles` | — | 文件同步（逐块传大文件用） |
| `debug.openTerminal` / `debug.updateTerminalSize` | — | 远程终端 |

手机→PC 方向的方法（本工具只记录，不实现）：`debug.clientLog`、`debug.debugEvent`、`rsync.getFile`、`rsync.syncCompletion`。

## 4. 协议要点（实现依据，供二次开发）

AutoJsPro 的调试通道是它自带的服务（"智能连接"的设备端），官方客户端是 VSCode 插件 `hyb1996.auto-js-pro-ext`
（插件监听 **21029**，用 mDNS 类型 `autojsprodebug` 发现设备）。逆向得到的契约：

- WebSocket，**只认二进制帧**（文本帧会被直接丢弃）；
- 帧 = **4 字节大端 int32 类型** + 载荷：`1` = JSON-RPC 2.0 文本，`2` = 二进制数据块（36 字节 id + 数据）；
- 每条 JSON 消息**根部带 `token`**（客户端 UUID）；连接后第一件事是 `debug.authorize`；
- 授权成功返回设备信息：`{"appVersion":"Pro 9.3.11-0","deviceId":…,"deviceName":…,"protocolVersion":1,"uuid":…}`。

## 5. 排障

| 症状 | 处理 |
|---|---|
| 找不到端口 | AutoJsPro 的「允许远程调试」没开 → 开关一次让它重新监听（端口会变，工具自动重扫） |
| `user rejected` | 授权被拒/超时 → 重跑 `auth`，请用户在手机上点「永久允许」 |
| `-32602 method parameters invalid` | 参数名不对：`debug.runFile` 用 **`{"file": …}`** |
| 连接后立刻被关闭 | 检查是否漏了 WS 帧封装（必须用二进制帧且客户端带掩码）——本工具已处理 |
| 脚本起来了但没反应 | AutoJsPro 被系统冻结 → 请用户开「前台服务」+ 自启动/省电无限制 |

## 6. 安全边界

- 本通道能在手机上**执行任意脚本**：涉及**支付、转账、发消息、删数据、改设置、退出登录**的动作**必须先问用户**；
- 只读操作（`vfs.readdir/readFile`、`listen`）可直接做；跑脚本前说清"要跑什么、做什么"；
- 不要用它绕过设备策略（DSHA 的 `[POLICY_BLOCKED]` 就是不能做，换通道也不行）；
- 不要把手机上的隐私内容外传（token、聊天记录、日志原文）。
