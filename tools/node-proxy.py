#!/usr/bin/env python3
"""app-node-proxy —— 借 AutoJsPro 的白名单无障碍服务，在任何应用里读/点无障碍节点。

为什么需要它：有些应用（微信是最典型的）只把无障碍节点暴露给「服务类名在白名单里」的服务
（典型白名单名 `com.google.android.accessibility.selecttospeak.SelectToSpeakService`）。
GKD / GKD-XA / 新版 AutoJsPro 用了这个名字；**DSHA 与 AutoX v7 没有** → 它们在这些应用里
读屏/截图/点击全失效（DSHA 桥表现为「你拒绝了这次…」）。

本工具不改 DSHA 的 APK，而是**调用 AutoJsPro**：
  手机侧运行 `/sdcard/脚本/ajpro-mcp-server.js`（HTTP MCP 服务，端口 6666，占用则顺延）
  → 容器 `POST http://127.0.0.1:<port>/` 调 `run_code`/`run_script`
  → 脚本把结果写 `/sdcard/脚本/_mcp-out.json` → 容器直读（不解析日志，最稳）。

用法：
  app-node-proxy.py ping                探活（自动扫描 6666-6675）
  app-node-proxy.py up                  确保服务在线：探活 → 不在就尝试用 DSHA 桥自动拉起
  app-node-proxy.py down [--hard]       关闭 MCP 服务（任务收尾必做）；--hard 兜底 force-stop AutoJsPro
  app-node-proxy.py dump                当前前台应用的节点快照（pkg/activity/文本/bounds/clickable）
  app-node-proxy.py shot [--out x.png]   用 AutoJsPro 截屏并存到容器（微信里 DSHA 桥截不了图时用这个）
  app-node-proxy.py tap <x> <y>          坐标点按（微信自身界面没有节点，只能这样点）
  app-node-proxy.py swipe x1 y1 x2 y2    滑动
  app-node-proxy.py key back|home|recents 按键
  app-node-proxy.py find 跳过            找文本或 desc 含关键词的节点
  app-node-proxy.py click 跳过           点第一个命中节点（不可点则点其中心坐标）
  app-node-proxy.py run '"rhino"; save({act: currentActivity()});'
  app-node-proxy.py engines             列出运行中的脚本引擎
  app-node-proxy.py stop <engineId>     停止某引擎（<engineId>=all 停全部子引擎）
  app-node-proxy.py start <脚本路径>     运行脚本文件（默认 wait=2s，脚本继续在后台跑）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

OUT = "/sdcard/脚本/_mcp-out.json"
PORTS = range(int(os.environ.get("AJPRO_PORT", "6666")), int(os.environ.get("AJPRO_PORT", "6666")) + 10)
BRIDGE = "http://127.0.0.1:3090"
TOKEN_FILE = "/root/.dsh/.bridge_token"
DEFAULT_MCP = "/sdcard/脚本/ajpro-mcp-server.js"
# 已知"对无障碍服务设白名单"的应用：DSHA 桥在里面必然被拒 → 直接用 MCP，别浪费时间试
RESTRICTED_PKGS = {
    "com.tencent.mm": "微信",
    "com.quark.browser": "夸克",
    "com.UCMobile": "UC",
    "com.eg.android.AlipayGphone": "支付宝",
    "com.taobao.taobao": "淘宝",
    "com.jingdong.app.mall": "京东",
}
_found_port: int | None = None


# ---------------------------------------------------------------- MCP

def rpc(method: str, params: dict | None = None, timeout: float = 60.0, port: int | None = None) -> dict:
    p = port or _found_port or PORTS.start
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{p}/", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def find_port(timeout: float = 6.0) -> int | None:
    global _found_port
    for p in PORTS:
        try:
            r = rpc("tools/list", timeout=timeout, port=p)
            if r.get("result", {}).get("tools"):
                _found_port = p
                return p
        except Exception:
            continue
    return None


def tool_text(resp: dict) -> str:
    try:
        return resp["result"]["content"][0]["text"]
    except Exception:
        return json.dumps(resp, ensure_ascii=False)


def call_tool(name: str, args: dict, timeout: float = 60.0) -> str:
    return tool_text(rpc("tools/call", {"name": name, "arguments": args}, timeout=timeout))


# ---------------------------------------------------------------- DSHA 桥（仅用于自动拉起）

def bridge(path: str, **params) -> str:
    params["token"] = open(TOKEN_FILE, encoding="utf-8").read().strip()
    url = f"{BRIDGE}/app/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=25) as resp:
        return resp.read().decode()


def _parse_row(dump: str, name: str):
    """在 AutoJsPro 的文件列表 dump 里找脚本行，返回 (top, bottom)。"""
    for ln in dump.splitlines():
        if name not in ln:
            continue
        if not re.search(r"(KB|MB|修改于|文件夹)", ln):      # 只认"文件列表行"，排除编辑器标题栏
            continue
        m = re.search(r"区域=(-?\d+),(-?\d+),(-?\d+),(-?\d+)", ln)
        if m:
            _, top, _, bottom = (int(v) for v in m.groups())
            return top, bottom
    return None


def _parse_run_button(dump: str, row):
    """取该行右侧最靠左的按钮（Auto.js Pro 文件列表里就是 ▶ 运行）。"""
    top, bottom = row
    cands = []
    for ln in dump.splitlines():
        m = re.search(r"可点击 中心=\((-?\d+),(-?\d+)\) 区域=(-?\d+),(-?\d+),(-?\d+),(-?\d+)", ln)
        if not m:
            continue
        cx, cy, x1, y1, x2, y2 = (int(v) for v in m.groups())
        if y1 >= top - 20 and y2 <= bottom + 20 and x1 > 600:
            cands.append((x1, cx, cy))
    if not cands:
        return None
    cands.sort()
    return cands[0][1], cands[0][2]


def ui_autostart(script: str = DEFAULT_MCP) -> str:
    """用 DSHA 桥在 AutoJsPro 界面里点一次"运行"。

    实测路径：launch → （必要时 back 退回主列表）→ （必要时向下滚动找行）
              → 点该行右侧最靠左的按钮（▶）。
    """
    name = os.path.basename(script)
    try:
        bridge("launch", pkg="org.autojs.autojspro")
        time.sleep(6)
    except Exception as e:
        return f"无法启动 AutoJsPro（{e.__class__.__name__}: {e}）"

    for i in range(6):
        try:
            dump = bridge("ui/dump")
        except Exception as e:
            return f"读屏失败（{e.__class__.__name__}: {e}）——微信在前台时桥会失效"
        row = _parse_row(dump, name)
        if row:
            btn = _parse_run_button(dump, row)
            if btn:
                try:
                    bridge("ui/tap", x=btn[0], y=btn[1])
                except Exception as e:
                    return f"点运行按钮失败（{e.__class__.__name__}: {e}）"
                return f"已点运行按钮 ({btn[0]},{btn[1]})（第 {i + 1} 轮）"
            try:
                bridge("ui/swipe", x1=540, y1=1800, x2=540, y2=700, ms=300)   # 行在但没按钮 → 滚一下
            except Exception:
                pass
            time.sleep(2.0)
            continue
        # 不在脚本列表：优先点「第 1 个标签」（实测最稳），没有标签栏才用返回键
        try:
            if "第 1 个标签" in dump:
                bridge("ui/tap", text="第 1 个标签，共 5 个")
            else:
                bridge("ui/key", name="back")
        except Exception:
            pass
        time.sleep(2.2)
    return f"界面上没找到「{name}」的可运行行（可手动打开 AutoJsPro 运行）"


def rpc_start(script: str = DEFAULT_MCP) -> tuple[bool, str]:
    """用 AutoJsPro 调试 RPC（/root/ajrpc.py run）远程启动脚本——无需 UI、无需人工点击。"""
    try:
        r = subprocess.run(["/root/ajrpc.py", "run", script],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return False, f"ajrpc 调用失败：{e.__class__.__name__}: {e}"
    out = (r.stdout or "") + (r.stderr or "")
    if "executionId" in out:
        return True, out.strip().splitlines()[-1][:120]
    return False, out.strip()[-200:] or "（无输出）"


def cmd_down(args) -> int:
    """停掉 MCP 服务（任务收尾用）。两条路：① MCP 自身引擎 forceStop ② 调试 RPC 的 debug.stopAll。"""
    if not find_port(timeout=3):
        print("ℹ️ MCP 服务已不在线，无需关闭")
        return 0
    self_id = None
    try:
        info = json.loads(tool_text(rpc("tools/call", {"name": "list_engines", "arguments": {}}, timeout=15)))
        for e in (info.get("engines") or []):
            if e.get("self"):
                self_id = e.get("id")
        if self_id is None:
            ids = [e.get("id") for e in (info.get("engines") or []) if e.get("id") is not None]
            self_id = min(ids) if ids else None
    except Exception as e:
        print(f"ℹ️ 取引擎列表失败（{e.__class__.__name__}），改用 RPC stopAll")
        self_id = None
    if self_id is not None:
        try:
            rpc("tools/call", {"name": "run_code",
                               "arguments": {"code": code_stop_engine(self_id), "wait": 1}}, timeout=15)
            print(f"→ 已请求停止 MCP 引擎（engineId={self_id}）")
        except Exception:
            pass
        for _ in range(10):
            time.sleep(0.6)
            if not find_port(timeout=2):
                print("✅ MCP 服务已关闭（AutoJsPro 进程保留，无障碍服务不受影响）")
                return 0
    # 兜底：用 AutoJsPro 调试 RPC 的 stopAll（会停掉手机侧所有脚本引擎，含 MCP）
    try:
        r = subprocess.run(["/root/ajrpc.py", "stopall"], capture_output=True, text=True, timeout=90)
        print("→ 已下发 debug.stopAll：" + ((r.stdout or "").strip().splitlines() or ["（无输出）"])[-1][:100])
    except Exception as e:
        print(f"   debug.stopAll 失败：{e.__class__.__name__}")
    for _ in range(10):
        time.sleep(0.8)
        if not find_port(timeout=2):
            print("✅ MCP 服务已关闭（经调试 RPC stopAll）")
            return 0
    if getattr(args, "hard", False):
        print("→ 兜底：force-stop AutoJsPro")
        try:
            which = shutil.which("adb-shell") or "/root/dsh-bin/adb-shell"
            subprocess.run([which, "am force-stop org.autojs.autojspro"], timeout=30, check=False)
        except Exception as e:
            print(f"   force-stop 失败：{e}")
        time.sleep(2)
        if not find_port(timeout=2):
            print("✅ AutoJsPro 已停止")
            return 0
    print("❌ 服务仍在运行；可重试 `down --hard`")
    return 1


def cmd_up(args) -> int:
    if find_port():
        print(f"✅ MCP 服务已在线（端口 {_found_port}）")
        return 0
    if not getattr(args, "no_rpc", False):
        print("ℹ️ 服务不在线，先用 AutoJsPro 调试 RPC 远程启动…")
        ok, info = rpc_start(args.script)
        print("   " + ("RPC 已下发：" + info if ok else "RPC 不可用：" + info))
        if ok:
            for _ in range(8):
                time.sleep(1.5)
                if find_port():
                    print(f"✅ 已起来（端口 {_found_port}）")
                    return 0
    print("ℹ️ 改用 DSHA 桥 UI 自动拉起…")
    msg = ui_autostart(args.script)
    print("  " + msg)
    for _ in range(8):
        time.sleep(2)
        if find_port():
            print(f"✅ 已起来（端口 {_found_port}）")
            return 0
    print("❌ 仍未起来。手动兜底：打开 AutoJsPro → 运行 " + args.script)
    print("   长期方案：AutoJsPro 设置里开「前台服务」，并给它 自启动=允许 / 省电=无限制；")
    print("   也可用 MCP 的 add_task 注册成开机/定时任务（见技能 app-node-proxy）。")
    return 2


# ---------------------------------------------------------------- 代码模板

PRELUDE = r'''"rhino";
var OUT = '/sdcard/脚本/_mcp-out.json';
function save(o) { files.write(OUT, JSON.stringify(o)); }
// AutoJsPro 的节点是 Java 属性式 API：childCount/clickable 是属性，getChildCount()/getChild() 是方法
function nKids(n) {
  var c = 0;
  try { c = (typeof n.childCount === "function") ? n.childCount() : n.childCount; } catch (e) {}
  if (typeof c !== "number") { try { c = n.getChildCount(); } catch (e) { c = 0; } }
  return (typeof c === "number" && c > 0) ? c : 0;
}
function nKid(n, i) {
  try { if (typeof n.child === "function") { var r = n.child(i); if (r) return r; } } catch (e) {}
  try { return n.getChild(i); } catch (e) {}
  return null;
}
function nText(n) {
  try { if (typeof n.text === "function") { var t = n.text(); if (t !== undefined) return t; } } catch (e) {}
  try { return n.getText(); } catch (e) {}
  return null;
}
function nDesc(n) {
  try { if (typeof n.desc === "function") { var d = n.desc(); if (d !== undefined) return d; } } catch (e) {}
  try { var d2 = n.contentDescription; if (d2 !== undefined) return d2; } catch (e) {}
  try { return n.getContentDescription(); } catch (e) {}
  return null;
}
function nCls(n) {
  try { if (typeof n.className === "function") { var c = n.className(); if (c) return c; } } catch (e) {}
  try { return n.getClassName(); } catch (e) {}
  return null;
}
function nClickable(n) {
  try { return (typeof n.clickable === "function") ? !!n.clickable() : !!n.clickable; } catch (e) {}
  try { return !!n.isClickable(); } catch (e) {}
  return false;
}
function nBounds(n) {
  // 节点是原生 AccessibilityNodeInfo：坐标必须用 getBoundsInScreen(Rect)（传一个 Rect 进去），
  // 没有 bounds() 方法、boundsInScreen 也不是可读属性。
  try { if (typeof n.bounds === "function") { var b0 = n.bounds(); if (b0) return b0; } } catch (e) {}
  try {
    var R = android.graphics.Rect, r = new R();
    n.getBoundsInScreen(r);
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  } catch (e) {}
  try {   // 兜底：解析 toString 里的 boundsInScreen
    var m = String(n).match(/boundsInScreen: Rect\((-?\d+), (-?\d+) - (-?\d+), (-?\d+)\)/);
    if (m) return { left: +m[1], top: +m[2], right: +m[3], bottom: +m[4] };
  } catch (e) {}
  return null;
}
function nCenter(n) {
  var b = nBounds(n);
  if (!b) return null;
  return { x: Math.round((b.left + b.right) / 2), y: Math.round((b.top + b.bottom) / 2), b: b };
}
function nId(n) {
  try { if (typeof n.id === "function") { var i = n.id(); if (i) return i; } } catch (e) {}
  try { return n.getViewIdResourceName(); } catch (e) {}
  return null;
}
function nPkg(n) {
  try { if (typeof n.packageName === "function") { var p = n.packageName(); if (p) return p; } } catch (e) {}
  try { return n.getPackageName(); } catch (e) {}
  return "?";
}
function screenSize() {
  var w = 1080, h = 2400;
  try { w = device.width || w; } catch (e) {}
  try { h = device.height || h; } catch (e) {}
  return { w: w, h: h };
}
function onScreen(b) {
  // 通用可视判定：覆盖"负值越界"和"超出屏宽"两种形态（微信 ViewPager 相邻页会被平移）
  if (!b) return false;
  var s = screenSize();
  return b.right > b.left && b.bottom > b.top &&
         b.left < s.w && b.right > 0 && b.top < s.h && b.bottom > 0;
}
function allRoots() {
  var arr = [], wins = null;
  try { wins = auto.windows; } catch (e) {}
  if (wins && wins.length) {
    for (var i = 0; i < wins.length; i++) { var r = null; try { r = wins[i].getRoot(); } catch (e) {} if (r) arr.push(r); }
  } else { arr.push(auto.rootInActiveWindow); }
  return arr;
}
function countAll() {
  function cnt(n, d) { if (!n || d > 30) return 0; var c = nKids(n), k = 1; for (var i = 0; i < c; i++) { var kk = nKid(n, i); if (kk) k += cnt(kk, d + 1); } return k; }
  var rs = allRoots(), t = 0;
  for (var j = 0; j < rs.length; j++) t += cnt(rs[j], 0);
  return t;
}
function hasKw(want) {
  if (!want) return null;
  function fnd(n, d) {
    if (!n || d > 30) return false;
    var t = nText(n), de = nDesc(n);
    if (t && String(t).indexOf(want) >= 0) return true;
    if (de && String(de).indexOf(want) >= 0) return true;
    var c = nKids(n);
    for (var i = 0; i < c; i++) { var kk = nKid(n, i); if (kk && fnd(kk, d + 1)) return true; }
    return false;
  }
  var rs = allRoots();
  for (var j = 0; j < rs.length; j++) if (fnd(rs[j], 0)) return true;
  return false;
}
function clickableAnc(n) {
  var cur = n, i = 0;
  if (nClickable(cur)) return cur;
  while (i < 8) { cur = nParent(cur); if (!cur) return null; if (nClickable(cur)) return cur; i++; }
  return null;
}
function nParent(n) {
  try { if (typeof n.parent === "function") { var p = n.parent(); if (p) return p; } } catch (e) {}
  try { return n.getParent(); } catch (e) {}
  return null;
}
'''


def code_dump(pkg_filter: str = "", keep_all: bool = False, clickable_only: bool = False, all_nodes: bool = False, rect: str = "", min_y: int = 0) -> str:
    """遍历**所有无障碍窗口**取节点；默认丢弃"不在屏内"的节点（噪音），可 --all 保留。"""
    pf = json.dumps(pkg_filter or "")
    ka = "true" if keep_all else "false"
    co = "true" if clickable_only else "false"
    an = "true" if all_nodes else "false"
    rc = (rect or "").strip()
    if rc:
        parts = [x.strip() for x in re.split(r"[,\s]+", rc) if x.strip()]
        rcj = "[%s]" % ",".join(parts[:4]) if len(parts) >= 4 else "null"
    else:
        rcj = "null"
    return PRELUDE + f'''
var pkgFilter = {pf}, keepAll = {ka}, clickableOnly = {co}, allNodes = {an};
var rectFilter = {rcj}, minY = {min_y};
function inRegion(b) {{
  if (!b) return false;
  if (minY && b.top < minY) return false;
  if (rectFilter) {{ if (b.bottom < rectFilter[1] || b.top > rectFilter[3] || b.right < rectFilter[0] || b.left > rectFilter[2]) return false; }}
  return true;
}}
var out = {{ pkg: currentPackage(), act: currentActivity(), windows: [], texts: [], total: 0,
             offscreen: 0, icons: 0, errs: [] }};
function walk(n, d, wpkg, layer) {{
  if (!n || d > 30 || out.total > 20000) return;
  out.total++;
  var t = nText(n), de = nDesc(n), cls = nCls(n), b = nBounds(n), clk = nClickable(n), id = nId(n);
  var ts = (t === null || t === undefined) ? "" : String(t);
  var ds = (de === null || de === undefined) ? "" : String(de);
  if (!(pkgFilter && wpkg !== pkgFilter)) {{
    var ons = onScreen(b) && inRegion(b);
    if (!ons) out.offscreen++;
    if (allNodes) {{
      if (ons || keepAll) {{
        out.texts.push({{ w: wpkg, l: layer, t: ts || null, d: ds || null, cls: cls, id: id,
                         depth: d, clk: clk, on: ons, icon: !(ts || ds), hasB: !!b,
                         b: b ? [b.left, b.top, b.right, b.bottom] : [0, 0, 0, 0] }});
      }}
    }} else if (clickableOnly) {{
      if (clk && ons) {{
        out.texts.push({{ w: wpkg, l: layer, t: ts || null, d: ds || null, cls: cls, id: id,
                         icon: !(ts || ds), on: ons,
                         b: b ? [b.left, b.top, b.right, b.bottom] : null, clk: clk }});
        if (!(ts || ds)) out.icons++;
      }}
    }} else if ((ts || ds) && (keepAll || ons)) {{
      out.texts.push({{ w: wpkg, l: layer, t: ts || null, d: ds || null, cls: cls, id: id,
                       icon: !(ts || ds), on: ons, hasB: !!b,
                       b: b ? [b.left, b.top, b.right, b.bottom] : [0, 0, 0, 0], clk: clk }});
    }}
  }}
  var c = nKids(n);
  for (var i = 0; i < c; i++) {{ var k = nKid(n, i); if (k) walk(k, d + 1, wpkg, layer); }}
}}
var wins = null;
try {{ wins = auto.windows; }} catch (e) {{ out.errs.push("windows: " + e); }}
if (wins && wins.length) {{
  for (var i = 0; i < wins.length; i++) {{
    var w = wins[i], r = null, wpkg = "?", act = false, foc = false, lay = -1;
    try {{ r = w.getRoot(); }} catch (e) {{}}
    if (r) {{ wpkg = nPkg(r); }}
    try {{ act = w.isActive(); }} catch (e) {{}}
    try {{ foc = w.isFocused(); }} catch (e) {{}}
    try {{ lay = w.getLayer(); }} catch (e) {{}}
    out.windows.push({{ i: i, pkg: wpkg, active: act, focused: foc, layer: lay, hasRoot: !!r }});
    if (r) walk(r, 0, wpkg, lay);
  }}
}} else {{
  out.errs.push("auto.windows 不可用，退回 rootInActiveWindow");
  walk(auto.rootInActiveWindow, 0, currentPackage(), -1);
}}
save(out);
log('MCP_DUMP_NODES=' + out.total + ' WINDOWS=' + out.windows.length);
'''


def code_find(kw: str, pkg_filter: str = "", keep_all: bool = False) -> str:
    """在所有无障碍窗口里找 text/desc 含关键词的节点；默认只保留"在屏内"的命中。"""
    k = json.dumps(kw, ensure_ascii=False)
    pf = json.dumps(pkg_filter or "")
    ka = "true" if keep_all else "false"
    return PRELUDE + f'''
var kw = {k}, pkgFilter = {pf};
var out = {{ pkg: currentPackage(), act: currentActivity(), kw: kw, hits: [], total: 0,
             offscreen: 0, keepAll: {ka} }};
function walk(n, d, wpkg) {{
  if (!n || d > 30 || out.total > 20000) return;
  out.total++;
  var t = nText(n), de = nDesc(n), cls = nCls(n), b = nBounds(n), clk = nClickable(n);
  var ts = (t === null || t === undefined) ? "" : String(t), ds = (de === null || de === undefined) ? "" : String(de);
  var hitT = ts.length > 0 && ts.indexOf(kw) >= 0, hitD = ds.length > 0 && ds.indexOf(kw) >= 0;
  if ((hitT || hitD) && (!pkgFilter || wpkg === pkgFilter)) {{
    var ons = onScreen(b);
    if (!ons) out.offscreen++;
    if (out.keepAll || ons) {{
      out.hits.push({{ w: wpkg, src: hitT ? "text" : "desc", t: ts || null, d: ds || null, cls: cls,
                      on: ons, clk: clk, anc: !!clickableAnc(n),
                      b: b ? [b.left, b.top, b.right, b.bottom] : null,
                      c: b ? [Math.round((b.left + b.right) / 2), Math.round((b.top + b.bottom) / 2)] : null }});
    }}
  }}
  var c = nKids(n);
  for (var i = 0; i < c; i++) {{ var k = nKid(n, i); if (k) walk(k, d + 1, wpkg); }}
}}
var wins = null;
try {{ wins = auto.windows; }} catch (e) {{}}
if (wins && wins.length) {{
  for (var i = 0; i < wins.length; i++) {{
    var r = null, wpkg = "?";
    try {{ r = wins[i].getRoot(); }} catch (e) {{}}
    if (r) {{ wpkg = nPkg(r); }}
    if (r) walk(r, 0, wpkg);
  }}
}} else {{ walk(auto.rootInActiveWindow, 0, currentPackage()); }}
save(out);
log('MCP_FIND_HITS=' + out.hits.length + ' / ' + out.total);
'''


def code_click(kw: str, pkg_filter: str = "", index: int = -1, verify: str = "") -> str:
    """找关键词节点并点击：候选过滤（离屏丢弃）→ 多命中列候选 → 可点祖先优先 → **点后回执校验**。

    返回里带 candidates[]（前 8 个候选，含 on/clk/bounds）、verify（节点数变化 + verify 关键词是否出现）、
    以及 warning —— 点了但页面没变化时会明确警告，不再静默 ok:true。
    """
    k = json.dumps(kw, ensure_ascii=False)
    pf = json.dumps(pkg_filter or "")
    vk = json.dumps(verify or "", ensure_ascii=False)
    idx = int(index)
    return PRELUDE + f'''
var kw = {k}, pkgFilter = {pf}, verifyKw = {vk}, wantIndex = {idx};
var out = {{ kw: kw, before: {{ pkg: currentPackage(), act: currentActivity() }}, candidates: [], hit: null }};
function collect(n, d, wpkg) {{
  if (!n || d > 30) return;
  var t = nText(n), de = nDesc(n), cls = nCls(n), b = nBounds(n), clk = nClickable(n);
  var ts = (t === null || t === undefined) ? "" : String(t);
  var ds = (de === null || de === undefined) ? "" : String(de);
  if ((ts.indexOf(kw) >= 0 || ds.indexOf(kw) >= 0) && (!pkgFilter || wpkg === pkgFilter)) {{
    out.candidates.push({{ w: wpkg, src: ts.indexOf(kw) >= 0 ? "text" : "desc",
                          t: ts || null, d: ds || null, cls: cls, clk: clk, anc: !!clickableAnc(n), on: onScreen(b),
                          b: b ? [b.left, b.top, b.right, b.bottom] : null }});
  }}
  var c = nKids(n);
  for (var i = 0; i < c; i++) {{ var kk = nKid(n, i); if (kk) collect(kk, d + 1, wpkg); }}
}}
function countNodes(n, d) {{
  if (!n || d > 30) return 0;
  var c = nKids(n), total = 1;
  for (var i = 0; i < c; i++) {{ var kk = nKid(n, i); if (kk) total += countNodes(kk, d + 1); }}
  return total;
}}
function hasText(n, d, want) {{
  if (!n || d > 30) return false;
  var t = nText(n), de = nDesc(n);
  if (t && String(t).indexOf(want) >= 0) return true;
  if (de && String(de).indexOf(want) >= 0) return true;
  var c = nKids(n);
  for (var i = 0; i < c; i++) {{ var kk = nKid(n, i); if (kk && hasText(kk, d + 1, want)) return true; }}
  return false;
}}
function roots() {{
  var arr = [], wins = null;
  try {{ wins = auto.windows; }} catch (e) {{}}
  if (wins && wins.length) {{
    for (var i = 0; i < wins.length; i++) {{
      var r = null; try {{ r = wins[i].getRoot(); }} catch (e) {{}}
      if (r) arr.push({{ r: r, pkg: nPkg(r) }});
    }}
  }} else {{ arr.push({{ r: auto.rootInActiveWindow, pkg: currentPackage() }}); }}
  return arr;
}}
// 1) 收集候选（带"在屏内"标记）
var rs = roots();
for (var i = 0; i < rs.length; i++) collect(rs[i].r, 0, rs[i].pkg);
var beforeNodes = 0;
for (var i2 = 0; i2 < rs.length; i2++) beforeNodes += countNodes(rs[i2].r, 0);

// 2) 选靶：在屏内优先 → clickable 优先；全部离屏时才退而用离屏候选（并警告）
var onCands = [], offCands = [];
for (var j = 0; j < out.candidates.length; j++) {{ (out.candidates[j].on ? onCands : offCands).push(j); }}
var pool = onCands.length ? onCands : offCands;
pool.sort(function (a, b) {{
  var A = out.candidates[a], B = out.candidates[b];
  var ra = (A.clk ? 2 : 0) + (A.anc ? 1 : 0), rb = (B.clk ? 2 : 0) + (B.anc ? 1 : 0);
  if (rb !== ra) return rb - ra;                       // 可点/有可点祖先的优先
  return a - b;                                        // 其余保持遍历序
}});
var pickIdx = (wantIndex >= 0 && pool[wantIndex] !== undefined) ? pool[wantIndex] : pool[0];
var target = null;
if (pickIdx !== undefined) {{
  var cand = out.candidates[pickIdx];
  out.pick = {{ index: pickIdx, on: cand.on, clk: cand.clk, t: cand.t, d: cand.d, b: cand.b, w: cand.w }};
  // 重新定位该节点（按 bounds+文本匹配）
  var want = cand.b;
  function locate(n, d, wpkg) {{
    if (!n || d > 30) return null;
    var b = nBounds(n), t = nText(n), de = nDesc(n);
    var ts = (t === null || t === undefined) ? "" : String(t);
    var ds = (de === null || de === undefined) ? "" : String(de);
    if (b && want && b.left === want[0] && b.top === want[1] && b.right === want[2] && b.bottom === want[3]
        && (ts.indexOf(kw) >= 0 || ds.indexOf(kw) >= 0) && (!pkgFilter || wpkg === pkgFilter)) return n;
    var c = nKids(n);
    for (var i = 0; i < c; i++) {{ var kk = nKid(n, i); if (kk) {{ var f = locate(kk, d + 1, wpkg); if (f) return f; }} }}
    return null;
  }}
  for (var m = 0; m < rs.length && !target; m++) target = locate(rs[m].r, 0, rs[m].pkg);
}}
out.candidateCount = out.candidates.length;
out.onscreenCount = onCands.length;
out.offscreenCount = offCands.length;

// 3) 点击：可点祖先 → 自身 → 坐标
if (target) {{
  var b2 = nBounds(target);
  out.hit = {{ w: nPkg(target), t: nText(target) === null ? null : String(nText(target)),
              d: nDesc(target) === null ? null : String(nDesc(target)), cls: nCls(target),
              b: b2 ? [b2.left, b2.top, b2.right, b2.bottom] : null,
              x: b2 ? Math.round((b2.left + b2.right) / 2) : null,
              y: b2 ? Math.round((b2.top + b2.bottom) / 2) : null }};
  var anc = clickableAnc(target), ok = false, how = "";
  if (anc) {{ try {{ ok = anc.click(); how = "ancestor"; }} catch (e) {{}} }}
  if (!ok) {{ try {{ ok = target.click(); how = "node"; }} catch (e) {{}} }}
  if (!ok && b2) {{
    var cx2 = Math.round((b2.left + b2.right) / 2), cy2 = Math.round((b2.top + b2.bottom) / 2);
    try {{ press(cx2, cy2, 60); ok = true; how = "coord-press"; }} catch (e) {{}}
    if (!ok) {{ try {{ gesture(60, [cx2, cy2], [cx2, cy2]); ok = true; how = "coord-gesture"; }} catch (e2) {{}} }}
    if (!ok) {{ try {{ click(cx2, cy2); ok = true; how = "coord-click"; }} catch (e3) {{}} }}
  }}
  out.clicked = {{ ok: !!ok, how: how }};
}} else {{
  out.warning = "没有找到可点的候选（关键词可能只在离屏/隐藏节点里）";
}}

// 4) 回执校验（治"静默点空"）：节点数变化 + verify 关键词
sleep(700);
var rs2 = roots(), afterNodes = 0, kws = null;
for (var n2 = 0; n2 < rs2.length; n2++) afterNodes += countNodes(rs2[n2].r, 0);
var verifyWaited = 0;
if (verifyKw) {{
  kws = false;
  var vdeadline = Date.now() + 6000;           // 渲染竞态：点后特征词可能要几秒才出现
  while (Date.now() < vdeadline) {{
    var rs3 = roots(), f3 = false;
    for (var n3 = 0; n3 < rs3.length; n3++) {{ if (hasText(rs3[n3].r, 0, verifyKw)) {{ f3 = true; break; }} }}
    if (f3) {{ kws = true; break; }}
    sleep(500);
  }}
  verifyWaited = 6000 - Math.max(0, vdeadline - Date.now());
}}
out.after = {{ pkg: currentPackage(), act: currentActivity() }};
out.verify = {{ nodesBefore: beforeNodes, nodesAfter: afterNodes, changed: afterNodes !== beforeNodes,
                transient: true, keyword: verifyKw || null, keywordFound: kws, waitedMs: verifyWaited }};
if (out.clicked && !out.verify.changed && (verifyKw === "" || kws === false)) {{
  out.warning = "点击后页面无变化（节点数 " + beforeNodes + "→" + afterNodes
    + (verifyKw ? "，且未出现「" + verifyKw + "」" : "") + "）：可能是①点空了（候选在离屏副本上）"
    + "②本来就已经在这个页面（幂等点击）。请用 --verify <下一页特征词> 再点一次以确认，或核对候选 bounds";
}}
if (!out.clicked && out.pick && !out.pick.on) {{
  out.warning = "命中的候选全部不在屏内（可能需要先 swipe 把它滚进可视区）";
}}
save(out);
log('MCP_CLICK candidates=' + out.candidateCount + ' on=' + out.onscreenCount + ' hit=' + (target ? 'yes' : 'no')
    + ' changed=' + out.verify.changed);
'''


def code_shot(path: str) -> str:
    """截屏：**无障碍截图优先**（auto.service.takeScreenshot，无弹窗、无需媒体投影授权，实测可用），
    失败才退回 requestScreenCapture/captureScreen（需前台授权）。"""
    return PRELUDE + f'''
var p = {json.dumps(path)};
var out = {{}};
function saveBitmap(bmp) {{
  try {{
    var fos = new java.io.FileOutputStream(p);
    bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, fos);
    fos.flush(); fos.close();
    out.ok = true; out.how = "a11y"; out.path = p; out.w = bmp.getWidth(); out.h = bmp.getHeight();
  }} catch (e) {{ out.saveErr = String(e).slice(0, 150); }}
}}
try {{
  if (auto && auto.service && typeof auto.service.takeScreenshot === "function") {{
    var ex = java.util.concurrent.Executors.newSingleThreadExecutor();
    var CB = android.accessibilityservice.AccessibilityService.TakeScreenshotCallback;
    var cb = new CB({{
      onSuccess: function (res) {{
        try {{
          var hb = res.getHardwareBuffer();
          var bmp = android.graphics.Bitmap.wrapHardwareBuffer(hb, res.getColorSpace());
          saveBitmap(bmp.copy(android.graphics.Bitmap.Config.ARGB_8888, false));
          hb.close();
        }} catch (e) {{ out.err = String(e).slice(0, 150); }}
      }},
      onFailure: function (code) {{ out.failCode = String(code); }}
    }});
    auto.service.takeScreenshot(android.view.Display.DEFAULT_DISPLAY, ex, cb);
    sleep(2200);
  }}
}} catch (e2) {{ out.err2 = String(e2).slice(0, 150); }}
// 兜底：媒体投影（需 AutoJsPro 前台授权）
if (!out.ok) {{
  try {{
    if (requestScreenCapture()) {{ sleep(400); var img = captureScreen(); if (img) {{ out.how = "mediaprojection"; out.w = img.getWidth(); out.h = img.getHeight();
      var fos2 = new java.io.FileOutputStream(p); img.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, fos2); fos2.flush(); fos2.close(); out.ok = true; }} }}
  }} catch (e3) {{ out.err3 = String(e3).slice(0, 120); }}
}}
out.pkg = currentPackage(); out.act = currentActivity();
save(out);
log('MCP_SHOT ok=' + out.ok + ' how=' + out.how);
'''


def code_tap(x: int, y: int, press_ms: int = 60, verify: str = "") -> str:
    """坐标点按：**真实触摸注入优先**（press/gesture）——小程序/WegView 里的卡片不响应无障碍 ACTION_CLICK。

    回执同样带节点数变化与 --verify 关键词，点了没反应会显式告警。
    """
    vk = json.dumps(verify or "", ensure_ascii=False)
    return PRELUDE + f'''
var x = {x}, y = {y}, pressMs = {press_ms}, verifyKw = {vk};
var out = {{ x: x, y: y, pressMs: pressMs, before: {{ pkg: currentPackage(), act: currentActivity() }} }};
var nodesBefore = countAll();
var how = "";
// ① 真实触摸（press）：小程序/WebView 只吃这种
try {{ press(x, y, pressMs); how = "press"; }} catch (e) {{ out.pressErr = String(e); }}
// ② 手势序列兜底
if (!how) {{ try {{ gesture(pressMs, [x, y], [x, y]); how = "gesture"; }} catch (e2) {{ out.gestureErr = String(e2); }} }}
// ③ 最后才是无障碍 click
if (!how) {{ try {{ click(x, y); how = "a11yClick"; }} catch (e3) {{ out.clickErr = String(e3); }} }}
out.how = how;
sleep(600);
var nodesAfter = countAll();
out.after = {{ pkg: currentPackage(), act: currentActivity() }};
var kwFound = null, kwWaited = 0;
if (verifyKw) {{
  var dl = Date.now() + 6000;
  kwFound = false;
  while (Date.now() < dl) {{ if (hasKw(verifyKw)) {{ kwFound = true; break; }} sleep(500); }}
  kwWaited = 6000 - Math.max(0, dl - Date.now());
}}
out.verify = {{ nodesBefore: nodesBefore, nodesAfter: nodesAfter, changed: nodesAfter !== nodesBefore,
                transient: true, keyword: verifyKw || null, keywordFound: kwFound, waitedMs: kwWaited }};
if (verifyKw && out.verify.keywordFound === false) {{
  out.warning = "点击后 6 秒内未出现「" + verifyKw + "」（节点数 " + beforeNodes + "→" + afterNodes
    + "）。判定：未生效——换候选/改坐标重试，或先 wait 等页面就绪再判定";
}} else if (verifyKw && out.verify.keywordFound === false) {{
  out.warning = "点按后 6 秒内未出现「" + verifyKw + "」。判定：未生效（换坐标/换按压时长重试，或先确认页面就绪）";
}} else if (!out.verify.changed && (verifyKw === "" || out.verify.keywordFound === false)) {{
  out.warning = "点按后页面无变化（" + nodesBefore + "→" + nodesAfter + "）。小程序里的卡片若仍无反应："
    + "该热区可能不响应触摸注入（换 --press-ms 300 或 --press-ms 900 各试一次即可，别再耗轮次）";
}}
save(out);
log('MCP_TAP how=' + how + ' changed=' + out.verify.changed);
'''


def code_swipe(x1: int, y1: int, x2: int, y2: int, ms: int) -> str:
    return PRELUDE + f'''
swipe({x1}, {y1}, {x2}, {y2}, {ms}); sleep(500);
save({{ ok: true, from: [{x1}, {y1}], to: [{x2}, {y2}], pkg: currentPackage(), act: currentActivity() }});
'''


def code_key(name: str) -> str:
    return PRELUDE + f'''
try {{ {name}(); }} catch (e) {{ save({{ ok: false, err: String(e) }}); }}
sleep(500);
save({{ ok: true, key: "{name}", pkg: currentPackage(), act: currentActivity() }});
'''


def code_wait(cond: str, timeout: float = 12.0) -> str:
    """通用等待原语：等到「就绪判据」成立再返回（替代裸 sleep）。cond 支持 kw:<文字> / act:<名> / nodes>N / stable。"""
    c = json.dumps(cond, ensure_ascii=False)
    return PRELUDE + f'''
var cond = {c}, deadline = Date.now() + {int(timeout * 1000)}, hit = false, kind = "cond";
function okNow() {{
  if (cond.indexOf("kw:") === 0) return hasKw(cond.slice(3));
  if (cond.indexOf("act:") === 0) {{ try {{ return String(currentActivity()).indexOf(cond.slice(4)) >= 0; }} catch (e) {{ return false; }} }}
  var m = cond.match(/^nodes\\s*(>=|>|==)\\s*(\\d+)$/);
  if (m) {{ var n = countAll(), v = parseInt(m[2], 10); return m[1] === ">=" ? n >= v : (m[1] === ">" ? n > v : n === v); }}
  return false;
}}
if (cond.indexOf("stable") === 0) kind = "stable";
var t0 = Date.now();
while (Date.now() < deadline) {{
  if (kind === "stable") {{ var a = countAll(); sleep(600); if (countAll() === a && a > 0) {{ hit = true; break; }} }}
  else if (okNow()) {{ hit = true; break; }}
  sleep(400);
}}
save({{ cond: cond, ok: hit, kind: kind, elapsedMs: Date.now() - t0, nodes: countAll(),
        act: currentActivity(), pkg: currentPackage() }});
log('MCP_WAIT ok=' + hit + ' ms=' + (Date.now() - t0));
'''


def code_hotspot(x1: int, y1: int, x2: int, y2: int, step: int, verify: str = "") -> str:
    """在矩形区域内网格轻点，找出真正能触发页面变化的坐标（解决"卡片热区与 a11y bounds 不一致"）。"""
    vk = json.dumps(verify or "", ensure_ascii=False)
    return PRELUDE + f'''
var verifyKw = {vk};
var startNodes = countAll(), tried = 0, hit = null, log2 = [];
var kw0 = verifyKw ? hasKw(verifyKw) : null;
for (var y = {y1}; y <= {y2} && !hit; y += {step}) {{
  for (var x = {x1}; x <= {x2} && !hit; x += {step}) {{
    tried++;
    try {{ press(x, y, 60); }} catch (e) {{}}
    sleep(450);
    var n = countAll();
    var kw = verifyKw ? hasKw(verifyKw) : null;
    if (n !== startNodes || (verifyKw && kw && !kw0)) {{
      hit = {{ x: x, y: y, nodes: n, keyword: verifyKw || null, keywordHit: kw }};
    }}
    if (tried % 20 === 0) log2.push(tried + "@" + x + "," + y);
  }}
}}
save({{ tried: tried, startNodes: startNodes, hit: hit, progress: log2 }});
log('MCP_HOTSPOT tried=' + tried + ' hit=' + (hit ? (hit.x + ',' + hit.y) : 'none'));
'''


def code_rows(pkg_filter: str = "", y1: int = 0, y2: int = 0, min_count: int = 3, tol: int = 8) -> str:
    """找"等宽同层兄弟行"（图标行/Tab 栏的通用几何特征）：返回每行的格子 bounds 与中心。

    判据（不依赖 clk）：同一父节点下 ≥min_count 个子节点，宽度一致、上下边界对齐。
    """
    pf = json.dumps(pkg_filter or "")
    return PRELUDE + f'''
var pkgFilter = {pf}, bandTop = {y1}, bandBottom = {y2}, minCount = {min_count}, tol = {tol};
var out = {{ pkg: currentPackage(), act: currentActivity(), rows: [], scanned: 0 }};
function kidsArr(n) {{ var a = [], c = nKids(n); for (var i = 0; i < c; i++) {{ var k = nKid(n, i); if (k) a.push(k); }} return a; }}
function span(a) {{ if (!a.length) return 0; var mn = a[0], mx = a[0]; for (var q = 1; q < a.length; q++) {{ if (a[q] < mn) mn = a[q]; if (a[q] > mx) mx = a[q]; }} return mx - mn; }}
function scan(n, d, wpkg) {{
  if (!n || d > 30) return;
  out.scanned++;
  var ks = kidsArr(n);
  if (ks.length >= minCount) {{
    var cells = [];
    for (var i = 0; i < ks.length; i++) {{
      var b = nBounds(ks[i]);
      if (!b) continue;
      if (b.right <= b.left || b.bottom <= b.top) continue;
      if (bandTop && b.top < bandTop) continue;
      if (bandBottom && b.bottom > bandBottom) continue;
      cells.push({{ cls: nCls(ks[i]), b: [b.left, b.top, b.right, b.bottom], w: b.right - b.left,
                   h: b.bottom - b.top, cx: Math.round((b.left + b.right) / 2), cy: Math.round((b.top + b.bottom) / 2) }});
    }}
    if (cells.length >= minCount) {{
      var ws = [], tops = [], bots = [], hs = [];
      for (var j = 0; j < cells.length; j++) {{ ws.push(cells[j].w); tops.push(cells[j].b[1]); bots.push(cells[j].b[3]); hs.push(cells[j].h); }}
      if (span(ws) <= tol && span(tops) <= tol && span(bots) <= tol && span(hs) <= tol) {{
        cells.sort(function (a, b2) {{ return a.b[0] - b2.b[0]; }});
        // 必须是"同行并列"：相邻格子横向互不重叠（否则是嵌套/堆叠节点，不是一行）
        var disjoint = true;
        for (var m1 = 1; m1 < cells.length; m1++) {{ if (cells[m1].b[0] < cells[m1 - 1].b[2] - tol) {{ disjoint = false; break; }} }}
        if (!disjoint) {{ cells = []; }}
        var gaps = [];
        for (var m = 1; m < cells.length; m++) gaps.push(cells[m].b[0] - cells[m - 1].b[2]);
        var pb = nBounds(n);
        if (cells.length) {{
          out.rows.push({{ parentCls: nCls(n), parentB: pb ? [pb.left, pb.top, pb.right, pb.bottom] : [0, 0, 0, 0],
                          count: cells.length, gaps: gaps, gapUniform: span(gaps) <= tol, cells: cells }});
        }}
      }}
    }}
  }}
  for (var q = 0; q < ks.length; q++) scan(ks[q], d + 1, wpkg);
}}
var roots = allRoots();
for (var i = 0; i < roots.length; i++) {{ var pk = nPkg(roots[i]); if (!pkgFilter || pk === pkgFilter) scan(roots[i], 0, pk); }}
save(out);
log('MCP_ROWS=' + out.rows.length);
'''


def code_raw(code: str) -> str:
    if "files.write" in code or "save(" in code:
        return PRELUDE + code
    return PRELUDE + code + "\nsave({ ok: true, pkg: currentPackage(), act: currentActivity() });\n"


def run_code(code: str, wait: int = 8, poll_s: float | None = None) -> dict:
    if not find_port():
        return {"ok": False, "hint": "MCP 服务不在线；先跑 app-node-proxy.py up，或在 AutoJsPro 里运行 ajpro-mcp-server.js"}
    try:
        os.remove(OUT)
    except FileNotFoundError:
        pass
    try:
        raw = call_tool("run_code", {"code": code, "wait": wait}, timeout=wait + 25)
    except Exception as e:
        return {"ok": False, "hint": f"调用失败：{e.__class__.__name__}: {e}（服务被冻结？把 AutoJsPro 切到前台或用 up）"}
    budget = int((poll_s if poll_s else wait * 4 + 12) / 0.25)
    for _ in range(max(12, budget)):
        if os.path.exists(OUT):
            try:
                data = json.load(open(OUT, encoding="utf-8"))
                if data:
                    return {"ok": True, "data": data, "raw": raw[:300]}
            except Exception:
                pass
        time.sleep(0.25)
    return {"ok": False, "raw": raw[:600], "hint": "没等到结果文件"}


def main() -> int:
    ap = argparse.ArgumentParser(description="借 AutoJsPro 读/点任意应用的无障碍节点")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    p_up = sub.add_parser("up"); p_up.add_argument("--script", default=DEFAULT_MCP)
    p_ensure = sub.add_parser("ensure"); p_ensure.add_argument("--script", default=DEFAULT_MCP)
    p_up.add_argument("--no-rpc", action="store_true", help="跳过调试 RPC，直接用 UI 自动化拉起")
    p_down = sub.add_parser("down"); p_down.add_argument("--hard", action="store_true",
                                                         help="兜底：force-stop AutoJsPro（可能需重新打开无障碍服务）")
    p_dump = sub.add_parser("dump"); p_dump.add_argument("--pkg", default="", help="只看某个包名的窗口，如 com.tencent.mm")
    p_dump.add_argument("--all", action="store_true", help="保留不在屏内的节点（默认丢弃这种噪音）")
    p_dump.add_argument("--clickable", action="store_true", help="只列可点击且在屏内的节点（含无文字的图标，标 [icon]）")
    p_dump.add_argument("--nodes", action="store_true", help="导出**所有**节点（含无文字/无 desc 的 Image、Tab 图标）")
    p_dump.add_argument("--rect", default="", help="只保留与该矩形相交的节点，格式 x1,y1,x2,y2")
    p_dump.add_argument("--min-y", type=int, default=0, help="只保留 top >= N 的节点（只看底部区域时用）")
    p_dump.add_argument("--json", action="store_true", help="直接输出完整 JSON（不打印摘要；数据同样在 /tmp/node-proxy-last.json）")
    p_find = sub.add_parser("find"); p_find.add_argument("keyword"); p_find.add_argument("--pkg", default="")
    p_find.add_argument("--all", action="store_true", help="保留不在屏内的命中（默认丢弃）")
    p_find.add_argument("--json", action="store_true")
    p_click = sub.add_parser("click"); p_click.add_argument("keyword"); p_click.add_argument("--pkg", default="")
    p_click.add_argument("--index", type=int, default=-1, help="选第几个候选（默认自动挑：在屏内→可点优先）")
    p_click.add_argument("--verify", default="", help="点击后应出现的关键词（复核用）")
    p_click.add_argument("--json", action="store_true")
    p_run = sub.add_parser("run"); p_run.add_argument("code")
    p_shot = sub.add_parser("shot"); p_shot.add_argument("--out", default=None, help="容器内保存路径（默认 /root/shot-<时间>.png）")
    sub.add_parser("shotpath")            # 只打印手机上的路径
    p_tap = sub.add_parser("tap"); p_tap.add_argument("x", type=int); p_tap.add_argument("y", type=int)
    p_tap.add_argument("--press-ms", type=int, default=60, help="按压时长：60=轻点 300=稍长 900=长按")
    p_tap.add_argument("--verify", default="", help="点按后应出现的关键词（回执校验）")
    p_sw = sub.add_parser("swipe"); p_sw.add_argument("x1", type=int); p_sw.add_argument("y1", type=int)
    p_sw.add_argument("x2", type=int); p_sw.add_argument("y2", type=int); p_sw.add_argument("--ms", type=int, default=400)
    p_key = sub.add_parser("key"); p_key.add_argument("name", choices=["back", "home", "recents", "notifications", "quicksettings"])
    p_route = sub.add_parser("route"); p_route.add_argument("pkg", help="目标应用包名，如 com.tencent.mm")
    p_ocr = sub.add_parser("ocr", help="无障碍截图 + 按区域轻量 OCR（给无文字的图标/卡片入口打标签）")
    p_ocr.add_argument("--pkg", default="")
    p_ocr.add_argument("--y", type=int, default=0, help="只 OCR 该 y 以下的区域（默认整屏）")
    p_ocr.add_argument("--rect", default="", help="显式矩形 x1,y1,x2,y2（不再按节点猜区域，最可靠）")
    p_ocr.add_argument("--full", action="store_true", help="整屏 OCR，不按节点区域裁剪")
    p_rows = sub.add_parser("rows", help="找等宽兄弟行（图标行/Tab 栏）→ 输出每格中心；--ocr 时逐格贴标签")
    p_rows.add_argument("--pkg", default="")
    p_rows.add_argument("--band", nargs=2, type=int, default=None, metavar=("Y1", "Y2"), help="只找该 y 波段内的行")
    p_rows.add_argument("--min-count", type=int, default=3)
    p_rows.add_argument("--ocr", action="store_true", help="对每格裁图做轻量 OCR，输出 #k 标签 @ (cx,cy)")
    p_rows.add_argument("--max-rows", type=int, default=3)
    p_wait = sub.add_parser("wait", help="等到就绪判据成立（替代裸 sleep）")
    p_wait.add_argument("--until", required=True, help='判据：kw:<特征词> / act:<activity> / nodes>N / stable')
    p_wait.add_argument("--timeout", type=float, default=12.0)
    p_hs = sub.add_parser("hotspot", help="网格试探找出真正能点的坐标（卡片热区对不上时用）")
    p_hs.add_argument("x1", type=int); p_hs.add_argument("y1", type=int)
    p_hs.add_argument("x2", type=int); p_hs.add_argument("y2", type=int)
    p_hs.add_argument("--step", type=int, default=60); p_hs.add_argument("--verify", default="")
    sub.add_parser("engines")
    p_stop = sub.add_parser("stop"); p_stop.add_argument("engine")
    p_start = sub.add_parser("start"); p_start.add_argument("script"); p_start.add_argument("--wait", type=int, default=2)
    args = ap.parse_args()

    if args.cmd == "rows":
        y1, y2 = (args.band if args.band else (0, 0))
        res = run_code(code_rows(args.pkg, y1, y2, args.min_count))
        d = res.get("data") or {}
        rows = d.get("rows") or []
        if not rows:
            print(f"未发现等宽兄弟行（扫过 {d.get('scanned')} 节点）")
            return 0
        shot = None
        need_ocr = args.ocr
        if need_ocr:
            r2 = run_code(code_shot("/sdcard/脚本/_mcp-shot.png"))
            if (r2.get("data") or {}).get("ok"):
                import shutil as _sh
                _sh.copyfile("/sdcard/脚本/_mcp-shot.png", "/root/a11y-shot.png")
                shot = "/root/a11y-shot.png"
            else:
                print("⚠️ 截图失败，只给几何不给标签")
        ocr = None
        if shot:
            try:
                from PIL import Image
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except Exception:
                    from rapidocr import RapidOCR
                ocr = RapidOCR()
                img = Image.open(shot)
            except Exception as e:
                print("⚠️ OCR 初始化失败:", e.__class__.__name__); ocr = None
        for ri, row in enumerate(rows[: args.max_rows]):
            cells = row.get("cells") or []
            print(f"行{ri + 1}：{row.get('count')} 格  父={str(row.get('parentCls'))[-20:]}  格宽={cells[0].get('w') if cells else '-'}"
                  f"  间距={row.get('gaps')}  等距={row.get('gapUniform')}  父bounds={row.get('parentB')}")
            for k, c in enumerate(cells):
                label = ""
                if ocr is not None:
                    b = c["b"]
                    crop = img.crop((max(0, b[0] - 2), max(0, b[1] - 2), min(img.width, b[2] + 2), min(img.height, b[3] + 2)))
                    crop.save("/tmp/_cell.png")
                    r, _ = ocr("/tmp/_cell.png")
                    label = " ".join(t[1] for t in (r or [])).strip()
                print(f"   #{k + 1} {('『' + label + '』') if label else '[' + str(c.get('cls'))[-12:] + ']'}"
                      f" @ ({c['cx']},{c['cy']})  b={c['b']}")
        if ocr is not None:
            print("（动作建议：tap <目标格中心x> <中心y> --press-ms 60 --verify <该格标签或目标页特征词>）")
        return 0
    if args.cmd == "wait":
        t0 = __import__("time").time()
        res = run_code(code_wait(args.until, args.timeout), wait=6, poll_s=args.timeout + 15)
        d = res.get("data") or {}
        flag = "✅ 已就绪" if d.get("ok") else "⏱️ 超时未满足"
        print(f"等待 {args.until}: {flag}（{d.get('elapsedMs')}ms，节点 {d.get('nodes')}，{d.get('pkg')}/{d.get('act')}）")
        return 0
    if args.cmd == "ocr":
        # ① 无障碍截图
        remote = "/sdcard/脚本/_mcp-shot.png"
        res = run_code(code_shot(remote))
        if not (res.get("ok") and (res.get("data") or {}).get("ok")):
            print("❌ 截图失败：", json.dumps(res, ensure_ascii=False)[:300]); return 1
        meta = res["data"]
        local = "/root/a11y-shot.png"
        import shutil as _sh
        _sh.copyfile(remote, local)
        # ② 取可点容器节点（拿 bounds）
        res2 = run_code(code_dump(args.pkg, False, False, True))   # 全部节点（含 clk=false 的图标行），不再只看 clickable
        nodes = (res2.get("data") or {}).get("texts") or []
        boxes = []
        for n in nodes:
            b = n.get("b")
            if b and b[2] > b[0] and b[3] > b[1] and (b[1] >= args.y):
                boxes.append((b, (n.get("t") or n.get("d") or "")[:20]))
        # ③ 裁区域 + 容器内 RapidOCR
        explicit = None
        if getattr(args, "rect", ""):
            part = [x.strip() for x in re.split(r"[,\s]+", args.rect) if x.strip()]
            if len(part) >= 4:
                explicit = [int(float(v)) for v in part[:4]]
        try:
            from PIL import Image
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception:
                from rapidocr import RapidOCR
            ocr = RapidOCR()
            img = Image.open(local)
            if explicit:
                regions = [(explicit, "(显式矩形)")]
            elif args.full or not boxes:
                regions = [([0, args.y, img.width, img.height], "(整屏)")]
            else:
                regions = []
                for b, label in boxes:
                    regions.append(([max(0, b[0] - 10), max(0, b[1] - 10), min(img.width, b[2] + 10), min(img.height, b[3] + 10)], label))
            print(f"无障碍截图 {meta.get('w')}x{meta.get('h')} → {local}；待识别区域 {len(regions)} 个")
            for b, label in regions[:24]:
                crop = img.crop(tuple(b))
                if crop.width < 8 or crop.height < 8:
                    continue
                crop.save("/tmp/_ocr_crop.png")
                r, _ = ocr("/tmp/_ocr_crop.png")
                txt = " ".join(t[1] for t in (r or [])).strip()
                if txt:
                    print(f"  b={b} 节点文本={label!r} → OCR: {txt[:60]}")
        except Exception as e:
            print("OCR 失败:", e.__class__.__name__, str(e)[:200])
        return 0
    if args.cmd == "hotspot":
        res = run_code(code_hotspot(args.x1, args.y1, args.x2, args.y2, args.step, args.verify), wait=0, poll_s=300)
        d = res.get("data") or {}
        print(f"试探 {d.get('tried')} 点（起始节点 {d.get('startNodes')}）")
        if d.get("hit"):
            h = d["hit"]
            print(f"🎯 命中热区：({h['x']},{h['y']}) 节点 {d.get('startNodes')}→{h['nodes']}"
                  + (f" 「{h.get('keyword')}」={h.get('keywordHit')}" if h.get("keyword") else ""))
        else:
            print("未找到可触发变化的点（可能整块区域都不可点，或需要更长按压/滚动）")
        return 0
    if args.cmd == "route":
        pkg = args.pkg
        if pkg in RESTRICTED_PKGS:
            print(f"{pkg}（{RESTRICTED_PKGS[pkg]}）→ 受限应用：**直接用 MCP 节点代理**（先 ensure），不要试 DSHA 桥")
            return 0
        print(f"{pkg} → 非受限：先用 DSHA 桥（客户端 5 秒超时）；读不到再 ensure 走 MCP")
        return 0
    if args.cmd == "ping":
        if find_port():
            names = [t["name"] for t in rpc("tools/list").get("result", {}).get("tools", [])]
            print(f"✅ MCP 在线（端口 {_found_port}），工具 {len(names)} 个")
            return 0
        print("❌ 未发现在线的 MCP 服务（扫了 6666-6675）。先 `up` 或手动运行 ajpro-mcp-server.js")
        return 2
    if args.cmd in ("up", "ensure"):
        return cmd_up(args)
    if args.cmd == "down":
        return cmd_down(args)
    if args.cmd == "engines":
        print(call_tool("list_engines", {}))
        return 0
    if args.cmd == "stop":
        arg = {"engineId": "all"} if args.engine == "all" else {"engineId": int(args.engine)}
        print(call_tool("stop_all_scripts" if args.engine == "all" else "stop_script", arg))
        return 0
    if args.cmd == "start":
        print(call_tool("run_script", {"path": args.script, "wait": args.wait}, timeout=args.wait + 25))
        return 0

    if args.cmd in ("shot", "shotpath"):
        remote = "/sdcard/脚本/_mcp-shot.png"
        res = run_code(code_shot(remote))
        if args.cmd == "shotpath":
            print(json.dumps(res, ensure_ascii=False, indent=2)[:800]); return 0 if res.get("ok") else 1
        if not res.get("ok") or not (res.get("data") or {}).get("ok"):
            print("❌ 截屏失败:", json.dumps(res, ensure_ascii=False)[:500]); return 1
        import shutil as _sh, time as _t
        out = args.out or f"/root/shot-{_t.strftime('%H%M%S')}.png"
        _sh.copyfile(remote, out)
        print(json.dumps({"ok": True, "saved": out, "meta": res["data"]}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "tap":
        res = run_code(code_tap(args.x, args.y, args.press_ms, args.verify))
        d = res.get("data") or {}
        print(f"点按 ({args.x},{args.y}) pressMs={args.press_ms} how={d.get('how')}")
        v = d.get("verify") or {}
        print(f"回执：节点 {v.get('nodesBefore')}→{v.get('nodesAfter')} changed={v.get('changed')}"
              + (f" 「{v.get('keyword')}」found={v.get('keywordFound')}" if v.get('keyword') else ""))
        if d.get("warning"):
            print(f"⚠️ {d['warning']}")
        print(f"（完整 JSON: /tmp/node-proxy-last.json）")
        return 0
    if args.cmd == "swipe":
        print(json.dumps(run_code(code_swipe(args.x1, args.y1, args.x2, args.y2, args.ms)), ensure_ascii=False)[:500]); return 0
    if args.cmd == "key":
        print(json.dumps(run_code(code_key(args.name)), ensure_ascii=False)[:500]); return 0
    if args.cmd == "dump":
        code = code_dump(args.pkg, args.all, args.clickable, getattr(args, "nodes", False),
                         getattr(args, "rect", ""), getattr(args, "min_y", 0))
    elif args.cmd == "find":
        code = code_find(args.keyword, args.pkg, args.all)
    elif args.cmd == "click":
        code = code_click(args.keyword, args.pkg, args.index, args.verify)
    else:
        code = code_raw(args.code)
    res = run_code(code)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False, indent=2)[:1500])
        return 1
    data = res.get("data") or {}
    # 完整结果落盘（避免终端截断），终端只打摘要
    try:
        with open("/tmp/node-proxy-last.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        saved = "/tmp/node-proxy-last.json"
    except Exception:
        saved = "(保存失败)"
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "dump":
        texts = data.get("texts") or []
        if getattr(args, "nodes", False):
            print(f"全部节点 {len(texts)} 个（离屏丢弃 {data.get('offscreen', 0)}）；只打 y>1800 的：")
            for t in texts:
                b = t.get("b") or [0, 0, 0, 0]
                if b[1] > 1800:
                    print(f"    depth={t.get('depth')} {str(t.get('cls'))[:28]:30} b={b} clk={t.get('clk')} t={t.get('t')}")
            return 0
        named = [t for t in texts if (t.get("t") or "").strip()]
        print(f"窗口 {len(data.get('windows') or [])} 个 / 节点 {data.get('total')} / 条目 {len(texts)}（有文字 {len(named)}"
              + (f"，纯图标 {data.get('icons')}" if data.get("icons") else "")
              + f"；离屏丢弃 {data.get('offscreen', 0)}）")
        for w in (data.get("windows") or []):
            print(f"  窗口#{w.get('i')} {w.get('pkg')} active={w.get('active')} layer={w.get('layer')}")
        for t in named[:30]:
            icon = " [icon]" if t.get("icon") or (not (t.get("t") or t.get("d"))) else ""
            print(f"    - {(t.get('t') or ('(desc)' + str(t.get('d') or '')))[:26]:28} b={t.get('b')} clk={t.get('clk')}{icon}")
        if len(named) > 30:
            print(f"    …另有 {len(named) - 30} 条，完整见 {saved}")
    elif args.cmd == "find":
        hits = data.get("hits") or []
        print(f"前台 {data.get('pkg')} / {data.get('act')}")
        print(f"命中 {len(hits)} 条（在屏内；离屏丢弃 {data.get('offscreen', 0)} 条，扫过 {data.get('total')} 节点）")
        for i, h in enumerate(hits[:20]):
            print(f"    #{i} [{h.get('src')}] {(h.get('t') or h.get('d') or '')[:28]:30} b={h.get('b')} clk={h.get('clk')}")
        if len(hits) > 20:
            print(f"    …完整见 {saved}")
    elif args.cmd == "click":
        print(f"前台 {data.get('before', {}).get('pkg')} / {data.get('before', {}).get('act')}")
        print(f"候选 {data.get('candidateCount')} 个（在屏 {data.get('onscreenCount')} / 离屏 {data.get('offscreenCount')}）")
        for i, c in enumerate((data.get("candidates") or [])[:8]):
            mark = "★" if (data.get("pick") or {}).get("b") == c.get("b") else " "
            print(f"   {mark}#{i} [{c.get('src')}] {(c.get('t') or c.get('d') or '')[:26]:28} on={c.get('on')} clk={c.get('clk')} b={c.get('b')}")
        if data.get("pick"):
            print(f"选中 #{data['pick'].get('index')}（on={data['pick'].get('on')} clk={data['pick'].get('clk')}）")
        if data.get("hit"):
            print(f"点击 {data['hit'].get('x')},{data['hit'].get('y')} → {json.dumps(data.get('clicked'), ensure_ascii=False)}")
        v = data.get("verify") or {}
        print(f"回执：节点 {v.get('nodesBefore')}→{v.get('nodesAfter')} changed={v.get('changed')}"
              + (f" 「{v.get('keyword')}」found={v.get('keywordFound')}" if v.get("keyword") else ""))
        if data.get("warning"):
            print(f"⚠️ {data['warning']}")
        print(f"（完整 JSON: {saved}）")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1)[:1200])
    return 0 if not data.get("warning") else 0


if __name__ == "__main__":
    raise SystemExit(main())
