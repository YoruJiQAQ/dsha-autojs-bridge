# dsha-autojs-bridge

[中文](README.md) | **English**

**Give DSHA (DeepSeek Harness Android, DSHA v0.1.7-rc2 (release), https://github.com/DSH-APP/DSHA) access to two Auto.js Pro capabilities:**

| Tool | Purpose | Channel |
|---|---|---|
| **`node-proxy.py`**<br>`find` / `click` / `rows` / `dump` … | Read nodes and perform taps in apps that **whitelist accessibility services by class name** (WeChat, Quark, Alipay, …). Highlights:<br>· `rows --ocr` locates **text-less icon entries** via **geometry × semantics** (equal-width sibling row + per-cell OCR labeling)<br>· **Real touch injection** (`press`) — works even for mini-program WebView (XWeb) layers<br>· **Accessibility screenshot** (`auto.service.takeScreenshot`) — **no dialog, no permission prompt**<br>· A **first-class peer** of DSHA's built-in computer-use (commands map 1:1: `dump/find/click/tap/swipe/key`) | AutoJsPro's **MCP HTTP service**<br>`127.0.0.1:6666` (falls back to the next free port) |
| **`ajrpc.py`**<br>`run` / `stopall` / `call` | **Run arbitrary scripts on the phone, read/write phone files, stop scripts — from the container.** i.e. "vibe coding on the phone".<br>· It is also the **launcher underneath** `node-proxy.py ensure` (RPC starts the MCP service, MCP does the reading/tapping)<br>· `down` / `stopall` when done — the AutoJsPro process and its accessibility service stay untouched | AutoJsPro's **debug RPC**<br>WebSocket, dynamic port, **tap “Allow permanently” once on the phone** |

Both tools complement each other: **one handles reading/tapping the UI, the other executes scripts.** Both require **Auto.js Pro 9.x** on the phone.

---

## 1. What problem does it solve

DSHA's built-in computer-use (`/app/ui/*`) works well for ordinary apps, but it **always fails** in these situations:

- Apps that **whitelist accessibility services by class name** (WeChat, Quark, Alipay, …): DSHA's `dump/tap/screenshot`
  all return `你拒绝了这次屏幕读取 / 截屏 / 点击` or hang until a 60-second timeout.
- **Text-less, `clickable=false` icon entries** inside mini-programs (bottom tab bars, grid cards): they cannot be
  read at all, and taps have no effect.

`app-node-proxy` (`node-proxy.py`) fills this gap:

- It borrows AutoJsPro's accessibility service (whose class name *is* on the whitelist) to **traverse every window**
  and fetch `pkg / activity / text / bounds / clickable`;
- `rows --ocr` combines **geometry** (equal-width sibling rows) with **semantics** (per-cell OCR) to locate icon entries;
- It clicks via **real touch injection** (`press`), which the WebView layer receives;
- It screenshots via **accessibility**, with **no permission dialog**.

`autojspro-rpc` (`ajrpc.py`) covers the other need — **making the phone execute a script** without tapping "Run" by hand:

- `ajrpc.py run /sdcard/脚本/your-script.js` → the phone runs it immediately;
- `ajrpc.py call vfs.readdir '{"path":"/sdcard/脚本"}'` → read/write phone files;
- `ajrpc.py stopall` → stop all script engines when a task ends.

---

## 2. Architecture

```
┌────────────────────────── Phone (Android) ─────────────────────────┐
│                                                                    │
│  DSHA app (proot Ubuntu container = where these tools run)          │
│    node-proxy.py ──HTTP/JSON-RPC──┐                                 │
│    ajrpc.py      ──WebSocket──────┼──┐                              │
│                                   │  │                              │
│       ┌───────────────────────────┘  │                              │
│       ▼                              ▼                              │
│  Auto.js Pro (accessibility service + Node engine)                  │
│    ├─ MCP service :6666  ← ajpro-mcp-server.js (phone-side script)  │
│    └─ Debug RPC (dynamic port, listening only while enabled)        │
│                                                                    │
│  Target apps (WeChat / Quark / …) ←── AutoJsPro node reads + taps   │
└────────────────────────────────────────────────────────────────────┘
```

The container and the phone **share the network namespace**, so `127.0.0.1:6666` inside the container *is* the
phone's own port.

---

## 3. Dependencies & prerequisites

| # | Dependency | Notes |
|---|---|---|
| 1 | **Android phone + DSHA app** | These tools run inside DSHA's container (proot Ubuntu, python3 ≥ 3.10) |
| 2 | **Auto.js Pro 9.x** (`org.autojs.autojspro`) | The phone-side executor. **Required**: ① accessibility service enabled; ② *Developer debugging → Allow remote debugging* enabled; ③ tap **“Allow permanently”** in the dialog on first connection<br>Tested version **9.3.11-0** (author: [@Azek431](https://github.com/Azek431)) |
| 3 | **Phone-side MCP script** `ajpro-mcp-server.js`<br>Source: [https://autojspro.cn/](https://autojspro.cn/), **archive name `ajpro-mcp-server最终版.zip`** | Lives at `/sdcard/脚本/ajpro-mcp-server.js`; run it in AutoJsPro so it listens on `0.0.0.0:6666`. **This repository does not include it** (copyright belongs to its author) — see [`phone/README.md`](phone/README.md) for how to obtain it and the interface contract |
| 4 | Python 3.10+ | Already present in the container; OCR features need `rapidocr_onnxruntime` (optional, below) |
| 5 | *(optional)* `rapidocr_onnxruntime` + `pillow` | Needed only by `rows --ocr` / `ocr`: `pip install rapidocr_onnxruntime pillow` |

> **Why Auto.js Pro is mandatory**: WeChat and similar apps only expose their node tree to accessibility services whose
> **class name** is on a whitelist (typically `com.google.android.accessibility.selecttospeak.SelectToSpeakService`).
> AutoJsPro happens to use exactly that name; DSHA's own service and AutoX v7 do not — hence they see nothing.
> `node-proxy.py` simply uses that legitimate channel.

---

## 4. Installation

### Option A — send this prompt to the agent inside DSHA (one-shot, recommended)

```text
Please install and verify dsha-autojs-bridge (a DSHA ↔ Auto.js Pro bridge). Steps:

1. Clone/unpack this repo to /root/dsha-autojs-bridge (git pull if it already exists).
2. Run `bash /root/dsha-autojs-bridge/install.sh`. It will:
   - install tools/node-proxy.py and tools/ajrpc.py into /root with the executable bit;
   - install skills/app-node-proxy and skills/autojspro-rpc into $DSH_HOME/skills/;
   - print a self-check (python version, whether the MCP service is online, whether the phone-side script exists).
3. Based on the self-check, walk me through the remaining prerequisites, one item at a time,
   telling me only what I must do on the phone:
   - Auto.js Pro → enable the accessibility service, and enable Developer debugging → Allow remote debugging;
   - whether /sdcard/脚本/ajpro-mcp-server.js exists on the phone; if not, follow phone/README.md
     to guide me through placing and running it.
4. Run `/root/ajrpc.py auth`: my phone will show an authorization dialog and I will tap “Allow permanently”.
   Wait for my confirmation.
5. Run `/root/node-proxy.py ensure` to start the MCP service, then `/root/node-proxy.py ping` to confirm it is online.
6. Finally verify with a read-only test: `/root/node-proxy.py dump --pkg com.tencent.mm`
   (with WeChat in the foreground) should return several hundred nodes. Show me the result and do NOT tap anything.

Rules: do not reinstall or re-sign any app; do not bypass device policies with adb/shell;
read-only operations are fine, but explain what you are about to tap before any tap.
```

### Option B — manual

```bash
git clone https://github.com/YoruJiQAQ/dsha-autojs-bridge.git
cd dsha-autojs-bridge
./install.sh            # install / upgrade (idempotent)
./install.sh --check    # self-check only
./install.sh --uninstall
```

### Option C — as a DSHA plugin (bundle)

This repository follows the DSHA bundle convention (`dsh.bundle.patch` in `package.json` plus `cordis.patch.yml`):

- Install it from DSHA's **Plugins page** (npm package name or git URL). On load it performs the same installation
  as `install.sh`;
- Or add this package to `dsh.profile.bundles` in your profile's `package.json` and restart.

> The bundle does exactly one thing — install the tools and skills. It registers no services and touches no other
> plugin rows, so it composes safely with any profile.

---

## 5. One-time phone setup

1. **Auto.js Pro**: enable the **accessibility service** (choose “Always allow”);
2. **Auto.js Pro → Developer debugging → Allow remote debugging = ON** (required by `ajrpc.py`);
3. **Phone-side MCP script**: put `ajpro-mcp-server.js` in `/sdcard/脚本/` and run it once in AutoJsPro (listens on 6666).
   For long-term reliability enable “Foreground service” and grant autostart / no battery restrictions;
4. **First authorization**: `/root/ajrpc.py auth` → tap **“Allow permanently”** on the phone
   (the token is stored in the container at `/root/.ajrpc-token`; it will not ask again).

---

## 6. Quick start

### 6.1 Reading / tapping inside WeChat (node-proxy)

```bash
/root/node-proxy.py route com.tencent.mm      # ask which channel to use (restricted apps answer: use MCP)
/root/node-proxy.py ensure                    # enter the ready state (starts MCP in ~2.6s, idempotent)
/root/node-proxy.py dump --pkg com.tencent.mm # node snapshot (pkg/act/text/bounds/clickable)
/root/node-proxy.py find 通讯录 --pkg com.tencent.mm
/root/node-proxy.py click 通讯录 --pkg com.tencent.mm --verify 朋友圈   # candidate picking + post-click receipt
/root/node-proxy.py down                      # always finish with this
```

Command reference:

| Command | Purpose |
|---|---|
| `ensure` / `ping` / `down` | start (idempotent) / probe / shut the MCP service down |
| `route <pkg>` | decide between the DSHA bridge and MCP |
| `dump [--pkg] [--nodes] [--clickable] [--rect x1,y1,x2,y2] [--min-y N] [--all] [--json]` | node snapshot |
| `find <word> [--pkg] [--all]` | find nodes whose text/desc matches (returns `on`/`clk`/`anc`/bounds/center) |
| `click <word> [--pkg] [--index N] [--verify word]` | click a node (candidate filtering + post-click receipt + 6s retry window) |
| `rows [--pkg] [--band y1 y2] [--ocr]` | **equal-width sibling rows → per-cell label and center** (icon rows / tab bars / grids) |
| `ocr [--pkg] [--rect x1,y1,x2,y2] [--y N] [--full]` | accessibility screenshot + local lightweight OCR |
| `tap <x> <y> [--press-ms 60] [--verify word]` | **real touch** tap (works in WebView layers) |
| `swipe x1 y1 x2 y2 [--ms]` / `key back\|home\|recents` | swipe / key press |
| `wait --until "kw:<word>\|act:<name>\|nodes>N\|stable" [--timeout]` | **readiness predicate** (instead of a bare sleep) |
| `hotspot x1 y1 x2 y2 [--step N]` | grid-probe for the real hit area (fallback) |
| `shot [--out path]` | accessibility screenshot (no dialog) |

### 6.2 Running scripts on the phone (ajrpc)

```bash
/root/ajrpc.py port                            # find the debug port (cached at /root/.ajrpc-port)
/root/ajrpc.py auth                            # first-time authorization (tap “Allow permanently”)
/root/ajrpc.py run /sdcard/脚本/hello.js       # run a script
/root/ajrpc.py stopall                         # stop all engines
/root/ajrpc.py call vfs.readdir '{"path":"/sdcard/脚本"}'
/root/ajrpc.py listen                          # print every frame received (troubleshooting)
```

> `node-proxy.py ensure` is literally `/root/ajrpc.py run /sdcard/脚本/ajpro-mcp-server.js`:
> **RPC brings the service up, MCP does the reading/tapping** — together they give you a fully automatic on/off switch.

---

## 7. Bundled skills (loaded automatically by the agent)

After installation, new DSHA sessions load them automatically when a task matches:

| Skill | When it applies |
|---|---|
| `app-node-proxy` | Reading / tapping in restricted apps (icon entries, click discipline, overlap invariants, troubleshooting) |
| `autojspro-rpc` | Running scripts on the phone, reading/writing phone files, stopping scripts (protocol, troubleshooting, safety) |

The skills encode many **field-tested contracts** (e.g. do not try the DSHA bridge first in WeChat; tap with real
touch; `--verify` has a 6-second retry window; locate icon entries with `rows --ocr` instead of guessing coordinates),
which dramatically reduce wasted attempts.

---

## 8. Repository layout

```
dsha-autojs-bridge/
├── README.md                      # Chinese docs
├── README.en.md                   # this file
├── LICENSE                        # MIT
├── .gitignore
├── install.sh                     # idempotent install / self-check / uninstall
├── package.json                   # DSHA bundle metadata (dsh.bundle.patch)
├── cordis.patch.yml               # bundle patch: load this plugin
├── lib/index.js                   # plugin: installs tools and skills (idempotent, warns instead of throwing)
├── tools/
│   ├── node-proxy.py              # read/tap in restricted apps (via MCP)
│   └── ajrpc.py                   # remote script execution / file access (via debug RPC)
├── skills/
│   ├── app-node-proxy/SKILL.md
│   └── autojspro-rpc/SKILL.md
├── phone/README.md                # phone-side MCP script: how to obtain it + interface contract
└── docs/
    ├── protocol.md                # protocol details for both channels (reverse-engineered)
    └── agent-install-prompt.md    # one-shot install prompt (same text, easy to copy)
```

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ensure` reports the RPC is unavailable | *Allow remote debugging* is off in AutoJsPro (toggle it once; the port changes and the tool rescans automatically) |
| Auth dialog shown but nothing happens / `user rejected` | Re-run `/root/ajrpc.py auth` and tap **“Allow permanently”** on the phone |
| `-32602 method parameters invalid` | `debug.runFile` takes **`{"file": …}`** (not `path`) |
| MCP is offline | `ajpro-mcp-server.js` is not running (or AutoJsPro was killed) → run it once on the phone, or retry `ensure` |
| In WeChat, `dump` returns “you denied this screen read” | You are still using the DSHA bridge — **that always happens in WeChat**; use `node-proxy.py` instead |
| Requests hang after switching apps | AutoJsPro was frozen by the system → enable “Foreground service” + autostart / no battery restrictions |
| `rows --ocr` complains about missing modules | `pip install rapidocr_onnxruntime pillow` |

---

## 10. Safety boundaries

- Together these tools can **tap anywhere in any app** and **execute arbitrary scripts on the phone**. Actions involving
  **payments, transfers, sending messages, deleting data, changing settings, or logging out MUST be confirmed by the user first**;
- Read-only operations (`dump` / `find` / `rows` / `shot` / `vfs.readdir`) are fine; every tap or write should be stated
  explicitly ("what and where");
- **Do not** use it to bypass device policies (DSHA's `[POLICY_BLOCKED]` means “not allowed” — a different channel does not change that);
- Do not exfiltrate private phone content (tokens, raw logs, chat history);
- Respect the target apps' terms of service and local law. This project only provides the technical channel; you are
  responsible for how you use it.

## 11. License

MIT (see [LICENSE](LICENSE)). The phone-side `ajpro-mcp-server.js` is not part of this repository; its copyright
belongs to its author.
