#!/bin/bash
# 🚀 Hermes ACP 增强补丁
# 用法: curl -fsSL https://raw.githubusercontent.com/zhazha-xiaoyezhu/hermes-agent/feat/acp-reasoning-effort/patch.sh | bash
# shellcheck disable=SC2317

set +e  # 手动控制错误

DIR="$HOME/.hermes/hermes-agent"
BRANCH="feat/acp-reasoning-effort"
PATCH="https://raw.githubusercontent.com/zhazha-xiaoyezhu/hermes-agent/feat/acp-reasoning-effort/hermes_acp_enhancement.patch"
OK=true

cd "$DIR" || exit 1

# 检测
if grep -q "stream_delta_callback" acp_adapter/server.py 2>/dev/null; then
    echo "✅ 已安装，跳过"
    exit 0
fi

echo "📥 同步官方最新代码..."
git fetch origin main 2>&1 | tail -1
git reset --hard origin/main 2>&1 | tail -1
# 不管 stash 报错，外面工具要恢复就让它恢复，反正 reset 完了不影响了
git stash pop 2>/dev/null || true

echo "📁 备份..."
cp -n acp_adapter/server.py acp_adapter/server.py.bak 2>/dev/null || true
cp -n acp_adapter/session.py acp_adapter/session.py.bak 2>/dev/null || true

echo "🌿 建立分支 $BRANCH ..."
git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH" 2>/dev/null

echo "📥 打补丁..."
if ! curl -fsSL "$PATCH" | patch -p1; then
    echo "❌ 补丁失败"
    exit 1
fi

git add acp_adapter/server.py acp_adapter/session.py
git commit -m "feat(acp): streaming + effort + show_thinking" --no-gpg-sign 2>/dev/null || true

echo ""
echo "✅ 完成！重启: hermes acp"
echo "   /effort /show_thinking"
