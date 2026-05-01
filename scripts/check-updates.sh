#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

echo "🔍 检查上游更新..."
echo ""

# 获取最新信息
git fetch upstream --quiet 2>/dev/null || echo "⚠️  无法获取 upstream"
git fetch webui --quiet 2>/dev/null || echo "⚠️  无法获取 webui"

# 检查 upstream
echo "📦 NousResearch/hermes-agent:"
UPSTREAM_COMMITS=$(git rev-list HEAD..upstream/main --count 2>/dev/null || echo "0")
if [ "$UPSTREAM_COMMITS" -gt 0 ]; then
    echo "   🆕 $UPSTREAM_COMMITS 个新提交"
    echo ""
    git log HEAD..upstream/main --oneline --no-decorate | head -5
else
    echo "   ✅ 无更新"
fi

echo ""

# 检查 webui（使用 master 分支）
echo "📦 nesquena/hermes-webui:"
WEBUI_LATEST=$(git log webui/master --oneline --no-decorate -1 2>/dev/null || echo "无法获取")
if [ "$WEBUI_LATEST" != "无法获取" ]; then
    echo "   最新提交: $WEBUI_LATEST"
    echo ""
    git log webui/master --oneline --no-decorate | head -5
else
    echo "   ⚠️  无法检查更新"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$UPSTREAM_COMMITS" -gt 0 ]; then
    echo "💡 运行 ./scripts/sync-upstream.sh 进行同步"
else
    echo "✅ 所有上游都是最新的"
fi
