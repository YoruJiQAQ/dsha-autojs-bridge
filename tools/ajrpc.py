#!/usr/bin/env python3
"""ajrpc —— 直连 AutoJsPro 的调试服务（"智能连接"设备端），用它的原生协议远程操作。

协议（从官方 VSCode 插件 hyb1996.auto-js-pro-ext 的 JS 里逆出来的）：
  · WebSocket，**只认二进制帧**（文本帧会被直接丢弃）；
  · 帧 = 4 字节大端 int32 类型 + 载荷；类型 1 = JSON-RPC 2.0，类型 2 = 二进制数据块（36 字节 id + 数据）；
  · 每条 JSON 消息**根部带 token**（插件的 UUID），连接后第一件事是 `debug.authorize`；
  · 服务端在手机上、端口动态（"允许远程调试"打开时才有），插件用 mDNS（服务类型 autojsprodebug）发现它。
手机侧方法（插件调用者）：debug.authorize / debug.runFile / debug.runProject / debug.stop / debug.stopAll /
  debug.openTerminal / debug.updateTerminalSize / vfs.readdir / rsync.syncFiles / rsync.writeFile / rsync.cancelSyncFiles
手机→PC 方法：debug.clientLog / debug.debugEvent / rsync.getFile / rsync.syncCompletion

用法：
  ajrpc.py port                     扫描并打印调试服务端口
  ajrpc.py auth                     发 debug.authorize（手机上会弹授权框，需用户点允许）
  ajrpc.py call <method> '<json>'   通用调用
  ajrpc.py run /sdcard/脚本/x.js     运行脚本（debug.runFile）
  ajrpc.py stopall                  停止所有脚本
  ajrpc.py listen                   只监听并打印收到的每一帧
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
import concurrent.futures
import re

STATE_FILE = "/root/.ajrpc-token"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
FRAME_JSON = 1
FRAME_BINARY = 2


def token() -> str:
    if os.path.exists(STATE_FILE):
        t = open(STATE_FILE).read().strip()
        if t:
            return t
    t = str(uuid.uuid4())
    with open(STATE_FILE, "w") as f:
        f.write(t)
    return t


def lan_ip() -> str:
    """取本机(手机)局域网 IP——容器与手机同网络栈，所以这里就是手机自己的 wlan 地址。"""
    import subprocess
    try:
        out = subprocess.run(["ip", "-4", "addr"], capture_output=True, text=True, timeout=5).stdout
        for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)", out):
            ip = m.group(1)
            if not ip.startswith("127.") and not ip.startswith("100."):
                return ip
    except Exception:
        pass
    return "192.168.3.54"


CACHE = "/root/.ajrpc-port"


def _cached_port() -> int | None:
    try:
        p = int(open(CACHE).read().strip())
    except Exception:
        return None
    s = socket.socket(); s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", p))
        return p
    except Exception:
        return None
    finally:
        s.close()


def find_port(lo: int = 32768, hi: int = 65535) -> int | None:
    cached = _cached_port()
    if cached:
        return cached
    def probe(p: int):
        s = socket.socket(); s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", p))
            s.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            d = s.recv(256)
            if b"Java-WebSocket" in d and b"101" in d.split(b"\r\n")[0]:
                return p
        except Exception:
            return None
        finally:
            s.close()
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=300) as ex:
        for r in ex.map(probe, range(lo, hi)):
            if r:
                try:
                    open(CACHE, "w").write(str(r))
                except Exception:
                    pass
                return r
    return None


class Conn:
    def __init__(self, port: int, host: str | None = None):
        host = host or os.environ.get("AJRPC_HOST") or lan_ip()
        self.host = host
        self.s = socket.socket(); self.s.settimeout(10)
        self.s.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            hdr += self.s.recv(1)
        self.handshake = hdr.decode("utf-8", "ignore")
        self.token = token()
        self.next_id = 1
        self.pending: dict[int, dict] = {}
        self.log: list[str] = []
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    # ---------- 帧 ----------
    def _ws_send(self, data: bytes, opcode: int = 2) -> None:
        """真正的 WebSocket 帧（客户端必须加掩码），opcode 2=二进制 10=pong 1=文本"""
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < (1 << 16):
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _send_frame(self, ftype: int, payload: bytes) -> None:
        """协议帧 = 4 字节大端类型 + 载荷，再套一层 WS 二进制帧"""
        self._ws_send(struct.pack(">i", ftype) + payload, opcode=2)

    def send_json_rpc(self, method: str, params: dict | None = None, rid: int | None = None) -> int:
        msg = {"jsonrpc": "2.0", "id": rid if rid is not None else self.next_id,
               "method": method, "params": params or {}, "token": self.token}
        if rid is None:
            self.next_id += 1
        self.pending[msg["id"]] = {"at": time.time(), "method": method}
        self._send_frame(FRAME_JSON, json.dumps(msg, ensure_ascii=False).encode())
        return msg["id"]

    def _read_frame(self, tmo: float = 1.0):
        self.s.settimeout(tmo)
        try:
            h = self.s.recv(2)
        except Exception:
            return None, None
        if len(h) < 2:
            return None, None
        op = h[0] & 0x0F
        masked = h[1] & 0x80
        n = h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self.s.recv(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self.s.recv(8))[0]
        m = self.s.recv(4) if masked else b"\x00\x00\x00\x00"
        data = b""
        while len(data) < n:
            c = self.s.recv(n - len(data))
            if not c:
                break
            data += c
        if masked:
            data = bytes(b ^ m[i % 4] for i, b in enumerate(data))
        return op, data

    def _reader(self) -> None:
        while self.alive:
            op, data = self._read_frame(1.0)
            if op is None:
                continue
            if op == 9:                                     # ping → pong
                self.log.append(f"<<ping {data[:60]!r}>>")
                try:
                    self._ws_send(b"", opcode=10)
                except Exception:
                    break
            elif op == 10:
                self.log.append(f"<<pong {data[:60]!r}>>")
            elif op == 1:
                self.log.append(f"<<TEXT {data[:300]!r}>>")
            elif op == 8:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else None
                self.log.append(f"<<close code={code} reason={data[2:200]!r}>>")
                break
            elif op == 2:                                   # 二进制帧
                if len(data) < 4:
                    continue
                ftype = struct.unpack(">i", data[:4])[0]
                body = data[4:]
                if ftype == FRAME_JSON:
                    try:
                        obj = json.loads(body.decode("utf-8", "ignore"))
                    except Exception:
                        self.log.append(f"<<json解析失败 {body[:120]!r}>>")
                        continue
                    if isinstance(obj, dict) and "method" in obj:
                        # 手机 → 我们 的请求：回一个空结果，避免它等超时
                        self.log.append(f"<<req {obj.get('method')} {json.dumps(obj.get('params'), ensure_ascii=False)[:200]}>>")
                        resp = {"jsonrpc": "2.0", "id": obj.get("id"), "result": {}, "token": self.token}
                        self._send_frame(FRAME_JSON, json.dumps(resp).encode())
                    else:
                        self.log.append(f"<<resp {json.dumps(obj, ensure_ascii=False)[:400]}>>")
                        if isinstance(obj, dict) and obj.get("id") in self.pending:
                            self.pending[obj["id"]]["resp"] = obj
                elif ftype == FRAME_BINARY:
                    self.log.append(f"<<binary id={body[:36]!r} len={len(body) - 36}>>")
                else:
                    self.log.append(f"<<未知帧类型 {ftype}>>")

    def wait(self, rid: int, timeout: float = 25.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.pending.get(rid, {}).get("resp"):
                return self.pending[rid]["resp"]
            time.sleep(0.2)
        return None

    def close(self) -> None:
        self.alive = False
        try:
            self.s.close()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]

    if cmd == "port":
        p = find_port()
        print(p if p else "未发现调试服务（先在 AutoJsPro 打开「允许远程调试」，必要时重开一次）")
        return 0 if p else 1

    port = None
    if os.environ.get("AJRPC_PORT"):
        port = int(os.environ["AJRPC_PORT"])
    if not port:
        port = find_port()
    if not port:
        print("未发现调试服务；请确认 AutoJsPro「允许远程调试」已开（开关一次可让它重新监听）")
        return 1
    host = os.environ.get("AJRPC_HOST") or lan_ip()
    print(f"调试服务端口: {port}，连接地址: {host}（token={token()[:8]}…）")
    c = Conn(port, host)
    print("握手:", c.handshake.splitlines()[0])

    if cmd == "listen":
        try:
            while True:
                time.sleep(1)
                while c.log:
                    print(c.log.pop(0))
        except KeyboardInterrupt:
            pass
        c.close()
        return 0

    if cmd == "auth":
        rid = c.send_json_rpc("debug.authorize", {"token": c.token})
        print("已发送 debug.authorize，等待手机授权…（请在手机上点「永久允许」）")
        r = c.wait(rid, 60)
        print("应答:", json.dumps(r, ensure_ascii=False) if r else "超时未收到应答")
        while c.log:
            print("  帧:", c.log.pop(0))
        c.close()
        return 0 if r else 1

    if cmd == "call":
        method = sys.argv[2]
        params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        rid = c.send_json_rpc(method, params)
        r = c.wait(rid, 30)
        print(json.dumps(r, ensure_ascii=False, indent=2) if r else "超时未收到应答")
        c.close()
        return 0 if r else 1

    if cmd == "run":
        path = sys.argv[2]
        rid = c.send_json_rpc("debug.runFile", {"file": path})
        r = c.wait(rid, 30)
        print(json.dumps(r, ensure_ascii=False) if r else "超时未收到应答")
        c.close()
        return 0 if r else 1

    if cmd == "stopall":
        rid = c.send_json_rpc("debug.stopAll", {})
        r = c.wait(rid, 20)
        print(json.dumps(r, ensure_ascii=False) if r else "超时未收到应答")
        c.close()
        return 0 if r else 1

    print("未知子命令:", cmd)
    c.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
