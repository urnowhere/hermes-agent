#!/usr/bin/env python3
"""
清理 ZAI 端点缓存并重新检测。

这个脚本会：
1. 删除 auth.json 中缓存的 ZAI 端点
2. 清理 base_url 中的空格
3. 下次启动时会重新检测端点（优先使用 Coding Plan）
"""

import json
import sys
from pathlib import Path

def clear_zai_cache():
    """清理 ZAI 端点缓存。"""
    hermes_home = Path.home() / ".hermes"
    auth_file = hermes_home / "auth.json"

    if not auth_file.exists():
        print(f"❌ 文件不存在: {auth_file}")
        return False

    # 读取 auth.json
    with open(auth_file, "r") as f:
        auth_data = json.load(f)

    modified = False

    # 1. 清理 providers.zai 中的 detected_endpoint
    if "providers" in auth_data and isinstance(auth_data["providers"], dict):
        if "zai" in auth_data["providers"]:
            zai_state = auth_data["providers"]["zai"]
            if isinstance(zai_state, dict) and "detected_endpoint" in zai_state:
                print(f"🔧 删除缓存的端点: {zai_state.get('detected_endpoint', {})}")
                del zai_state["detected_endpoint"]
                modified = True

    # 2. 清理 credential_pool.zai 中的 base_url（去除空格）
    if "credential_pool" in auth_data and isinstance(auth_data["credential_pool"], dict):
        if "zai" in auth_data["credential_pool"]:
            zai_pool = auth_data["credential_pool"]["zai"]
            if isinstance(zai_pool, list):
                for entry in zai_pool:
                    if isinstance(entry, dict) and "base_url" in entry:
                        old_url = entry["base_url"]
                        new_url = old_url.strip()
                        if old_url != new_url:
                            print(f"🔧 清理 base_url: '{old_url}' -> '{new_url}'")
                            entry["base_url"] = new_url
                            modified = True

    # 3. 如果没有修改，提示用户
    if not modified:
        print("✅ 没有发现需要清理的缓存")
        return True

    # 4. 保存修改后的 auth.json
    with open(auth_file, "w") as f:
        json.dump(auth_data, f, indent=2, ensure_ascii=False)

    print(f"✅ 已清理缓存并更新: {auth_file}")
    print("📝 下次启动 hermes 时会重新检测端点（优先使用 Coding Plan）")
    return True

if __name__ == "__main__":
    try:
        success = clear_zai_cache()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
