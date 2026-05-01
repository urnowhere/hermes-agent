#!/bin/bash
# Hermes Agent Chat UI 启动脚本

set -e

echo "🚀 启动 Hermes Agent Chat UI..."
echo "================================"

# 设置环境变量
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

# 检查虚拟环境
if [ ! -f .venv/bin/python ]; then
    echo "❌ 虚拟环境不存在"
    exit 1
fi

echo "✅ 虚拟环境已就绪"
echo "✅ Python 版本: $(.venv/bin/python --version)"
echo "✅ 项目版本: 0.11.0"
echo ""

# 检查 Web Chat API 模块
echo "检查 Web Chat API 模块..."
.venv/bin/python -c "from hermes_cli.web_chat_api import auth, chat_stream, session_adapter; print('✅ Web Chat API 模块加载成功')" || {
    echo "❌ Web Chat API 模块加载失败"
    exit 1
}

echo ""
echo "================================"
echo "📝 启动选项:"
echo "  1. hermes web              - 启动 Web Chat UI"
echo "  2. hermes                  - 启动交互式 CLI"
echo "  3. hermes setup            - 运行设置向导"
echo "================================"
echo ""

# 启动 Web Chat UI
echo "🌐 启动 Web Chat UI..."
echo "访问地址: http://localhost:8000"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

.venv/bin/hermes web
