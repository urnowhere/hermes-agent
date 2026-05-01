#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

echo "📦 当前分支: $(git branch --show-current)"
echo ""

# ============================================
# 1. 同步 NousResearch/hermes-agent
# ============================================
echo "🔄 同步上游 hermes-agent..."
git fetch upstream

UPSTREAM_COMMITS=$(git rev-list HEAD..upstream/main --count 2>/dev/null || echo "0")
if [ "$UPSTREAM_COMMITS" -eq 0 ]; then
    echo "✅ 上游无更新"
else
    echo "📥 发现 $UPSTREAM_COMMITS 个新提交"
    if git merge upstream/main --no-edit; then
        echo "✅ 合并成功"
    else
        echo "⚠️  合并冲突，需要手动解决"
        exit 1
    fi
fi

# ============================================
# 2. 同步 nesquena/hermes-webui（手动方式）
# ============================================
echo ""
echo "🔄 同步 hermes-webui..."

# 检查 webui 本地仓库是否存在
WEBUI_LOCAL="/media/liu/文件/IDEWorkplaces/GitHub/hermes-webui"
if [ ! -d "$WEBUI_LOCAL" ]; then
    echo "⚠️  未找到 hermes-webui 本地仓库: $WEBUI_LOCAL"
    echo "   跳过 webui 同步"
else
    # 拉取最新代码
    echo "   拉取 webui 最新代码..."
    (cd "$WEBUI_LOCAL" && git pull origin master --quiet)

    # 同步文件
    echo "   同步文件到 web_chat_dist/..."
    rsync -av --delete \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "$WEBUI_LOCAL/static/" \
        "$REPO_ROOT/hermes_cli/web_chat_dist/"

    # 检查是否有变化
    if git diff --quiet hermes_cli/web_chat_dist/; then
        echo "✅ webui 无更新"
    else
        echo "📝 提交 webui 更新..."
        git add hermes_cli/web_chat_dist/
        git commit -m "sync: update web_chat_dist from hermes-webui $(cd $WEBUI_LOCAL && git rev-parse --short HEAD)"
        echo "✅ webui 同步成功"
    fi
fi

# ============================================
# 3. 运行测试
# ============================================
echo ""
echo "🧪 运行测试..."
if [ -d "tests/web_chat" ]; then
    if python -m pytest tests/web_chat/ -v; then
        echo "✅ 测试通过"
    else
        echo "⚠️  测试失败，但继续推送"
    fi
else
    echo "⚠️  未找到测试目录，跳过测试"
fi

# ============================================
# 4. 推送到你的 Fork
# ============================================
echo ""
echo "📤 推送到 origin..."
if git push origin $(git branch --show-current); then
    echo "✅ 推送成功"
else
    echo "⚠️  推送失败，请检查网络或权限"
    exit 1
fi

echo ""
echo "✅ 同步完成！"
echo ""
echo "📊 同步摘要："
echo "   - 上游提交: $UPSTREAM_COMMITS"
echo "   - 当前分支: $(git branch --show-current)"
echo "   - 最新提交: $(git log -1 --oneline)"
