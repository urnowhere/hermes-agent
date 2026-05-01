#!/bin/bash
# Hermes Web Server 启动脚本 - 端口 9119

set -e

echo "🚀 启动 Hermes Web Server..."
echo "================================"
echo "端口: 9119"
echo "地址: http://localhost:9119"
echo "Chat UI: http://localhost:9119/chat"
echo "================================"
echo ""

# 设置环境变量
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

# 启动服务
.venv/bin/hermes web --port 9119 --host 0.0.0.0 --no-open
