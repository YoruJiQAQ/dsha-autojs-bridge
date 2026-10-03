# 一键安装提示词（复制给 DSHA 里的 agent）

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
