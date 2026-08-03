#!/bin/bash
# NexSandglass V3.4.1 — macOS/Linux 安装脚本
set -e

echo "╔══════════════════════════════════╗"
echo "║  NexSandglass V3.4.1 安装程序    ║"
echo "║  沙漏记忆 · 跨会话 · 零外部依赖  ║"
echo "╚══════════════════════════════════╝"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi
echo "✅ Python: $(python3 --version)"

# 创建数据目录
for d in scripts persona archive; do
    mkdir -p "$HOME/.neurobase/$d"
done
echo "✅ 数据目录已创建 ($HOME/.neurobase)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 安装 Python 包（pip 优先，失败则提示 PYTHONPATH 方式）
if [ -d "$SCRIPT_DIR/nexsandglass" ]; then
    python3 -m pip install "$SCRIPT_DIR" || echo "⚠️ pip 安装失败，可将仓库目录加入 PYTHONPATH 使用"
    echo "✅ NexSandglass Python 包已安装"
else
    echo "⚠️ 未找到 nexsandglass 包目录，跳过安装（请使用完整仓库）"
fi

# 部署 Hermes 插件
PLUGIN_MEM="$HOME/.hermes/plugins/memory/nexsandglass"
PLUGIN_GW="$HOME/.hermes/plugins/sandglass"
mkdir -p "$PLUGIN_MEM" "$PLUGIN_GW"
if [ -f "$SCRIPT_DIR/nexsandglass/core/memory_provider.py" ]; then
    cp "$SCRIPT_DIR/nexsandglass/core/memory_provider.py" "$PLUGIN_MEM/__init__.py"
fi
if [ -f "$SCRIPT_DIR/nexsandglass/interfaces/plugin.py" ]; then
    cp "$SCRIPT_DIR/nexsandglass/interfaces/plugin.py" "$PLUGIN_GW/__init__.py"
fi
echo "✅ Hermes 插件已部署（memory_provider + gateway）"

echo ""
echo "🚀 重启 Hermes Gateway 即可自动落沙"
echo "🌡️ 多 profile: export NEXSANDBASE_HOME=~/.neurobase-custom"
echo ""
