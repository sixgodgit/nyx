#!/bin/bash
# Nyx 夜神 —— 从源码仓库安装（macOS / Linux）
#
# 一般用户直接：pip install nyx-memory（或带语义检索：pip install "nyx-memory[vector]"）
# 这个脚本用于从克隆下来的仓库安装：
#   ./install.sh             # 基础安装（零依赖）
#   ./install.sh --vector    # 加语义检索（sentence-transformers + 本地多语言模型，首次运行下载约 470MB）
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "❌ 没找到 $PY。需要 Python 3.10 或更新版本（可用 PYTHON=/path/to/python 指定）"
    exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "❌ 需要 Python 3.10 或更新版本，当前是 $("$PY" --version 2>&1)"
    exit 1
fi

EXTRAS=""
[ "$1" = "--vector" ] && EXTRAS="[vector]"
"$PY" -m pip install ".${EXTRAS}"

# v3.x 的旧安装脚本会把文件复制成 Hermes 插件。现在插件由 pip 自动注册（entry point），
# 旧文件留着会和新插件同时生效 —— 尤其旧的网关插件会在消息到达时再写一遍，用户的话被记两遍。
HH="${HERMES_HOME:-$HOME/.hermes}"
for old in "$HH/plugins/memory/nexsandglass" "$HH/plugins/sandglass"; do
    if [ -e "$old" ]; then
        echo "⚠️  发现旧版安装留下的 $old"
        echo "    请删除它（rm -rf \"$old\"），否则同一句话可能被记两遍"
    fi
done

echo ""
echo "✅ 已安装 nyx-memory $("$PY" -c 'import nexsandglass; print(nexsandglass.__version__)')"
echo "   Hermes：config.yaml 里设 memory.provider: nyx，然后重启 Hermes"
echo "   MCP：   $PY -m nexsandglass.interfaces.sandglass_mcp"
echo "   自检：  $PY -m nexsandglass.doctor"
