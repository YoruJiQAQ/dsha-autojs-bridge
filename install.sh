#!/usr/bin/env bash
# dsha-autojs-bridge 安装脚本（幂等，可重复执行）
#   把两个容器侧工具装到 $TOOLS_DIR（默认 /root），把两个技能装到 $DSH_HOME/skills。
# 用法：
#   ./install.sh                 # 安装/升级
#   ./install.sh --uninstall     # 卸载（只删本仓库装进去的文件）
#   ./install.sh --check         # 只做环境自检，不写文件
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${TOOLS_DIR:-/root}"
DSH_HOME_DIR="${DSH_HOME:-$HOME/.dsh}"
SKILLS_DIR="$DSH_HOME_DIR/skills"
MODE="install"

for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE="uninstall" ;;
    --check)     MODE="check" ;;
    -h|--help)   sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

blue() { printf '\033[36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✅ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠️  %s\033[0m\n' "$*"; }
die()  { printf '\033[31m❌ %s\033[0m\n' "$*" >&2; exit 1; }

TOOLS=(node-proxy.py ajrpc.py)
SKILLS=(app-node-proxy autojspro-rpc)

# ---------- 环境自检 ----------
check() {
  blue "== 环境自检 =="
  command -v python3 >/dev/null || die "缺少 python3"
  python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null \
    || warn "python3 版本低于 3.10，工具可能无法运行"
  ok "python3 $(python3 -c 'import platform;print(platform.python_version())')"

  if [ -d "$DSH_HOME_DIR" ]; then ok "DSH home: $DSH_HOME_DIR"; else warn "未发现 $DSH_HOME_DIR（技能会装到那里，稍后由 DSH 扫描）"; fi

  local bridge="/root/.dsh/.bridge_token"
  if [ -f "$bridge" ]; then ok "DSHA 设备桥 token 存在（/app/* 可用）"; else warn "未发现 $bridge —— 不是 DSHA 容器？app-node-proxy 的 DSHA 相关说明可忽略"; fi

  if python3 -c 'import socket;s=socket.socket();s.settimeout(1);s.connect(("127.0.0.1",6666));print()' 2>/dev/null; then
    ok "AutoJsPro MCP 服务在线（127.0.0.1:6666）"
  else
    warn "AutoJsPro MCP 服务未在线 —— 需要：手机装 Auto.js Pro 9.x，并运行手机侧脚本 ajpro-mcp-server.js（见 phone/README.md）"
  fi
}

case "$MODE" in
  check) check; exit 0 ;;
  uninstall)
    blue "== 卸载 =="
    for t in "${TOOLS[@]}"; do rm -f "$TOOLS_DIR/$t" && echo "  删除 $TOOLS_DIR/$t"; done
    for s in "${SKILLS[@]}"; do rm -rf "$SKILLS_DIR/$s" && echo "  删除 $SKILLS_DIR/$s"; done
    ok "已卸载（手机侧的 MCP 脚本与 AutoJsPro 设置需自行处理）"
    exit 0 ;;
esac

blue "== 安装到 $TOOLS_DIR 与 $SKILLS_DIR =="
mkdir -p "$TOOLS_DIR" "$SKILLS_DIR"

for t in "${TOOLS[@]}"; do
  src="$REPO_DIR/tools/$t"
  [ -f "$src" ] || die "缺少 $src"
  install -m 755 "$src" "$TOOLS_DIR/$t"
  ok "工具 $TOOLS_DIR/$t"
done

for s in "${SKILLS[@]}"; do
  src="$REPO_DIR/skills/$s"
  [ -d "$src" ] || die "缺少 $src"
  mkdir -p "$SKILLS_DIR/$s"
  cp -f "$src"/*.md "$SKILLS_DIR/$s/"
  ok "技能 $SKILLS_DIR/$s/"
done

# 手机侧 MCP 脚本：若手机上已有则提示路径，不覆盖
if [ -f "/sdcard/脚本/ajpro-mcp-server.js" ]; then
  ok "手机侧 MCP 脚本已存在：/sdcard/脚本/ajpro-mcp-server.js"
else
  warn "手机侧 MCP 脚本未就位：请按 phone/README.md 把它放到手机 /sdcard/脚本/ajpro-mcp-server.js"
fi

echo
check
echo
blue "下一步（手机上一分钟）："
echo "  1) Auto.js Pro → 无障碍服务 = 开；开发者调试 → 允许远程调试 = 开"
echo "  2) 首次连接：$TOOLS_DIR/ajrpc.py auth  → 手机弹框点「永久允许」"
echo "  3) 起 MCP：$TOOLS_DIR/node-proxy.py ensure   → 之后 dump/find/click/rows 都能用"
ok "安装完成"
