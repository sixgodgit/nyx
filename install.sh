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

echo ""
echo "✅ 已安装 nyx-memory $("$PY" -c 'import nexsandglass; print(nexsandglass.__version__)')"

# Hermes：0.19.0 只按目录找记忆插件、不看 pip 入口点，官方安装脚本还把它装在单独的虚拟环境里 ——
# nexsandglass_hermes install 找到它用的 Python、装进去、放入口文件、设 memory.provider（旧版残留也会报出来）
if command -v hermes >/dev/null 2>&1; then
    echo ""
    "$PY" -m nexsandglass_hermes install ${EXTRAS:+--vector} || echo "⚠️  Hermes 那一步没做完，见上面的提示"
else
    echo "   Hermes：装好 Hermes 之后运行 $PY -m nexsandglass_hermes install，然后重启 Hermes"
fi
echo "   MCP：   $PY -m nexsandglass.interfaces.sandglass_mcp"
echo "   自检：  $PY -m nexsandglass.doctor"
