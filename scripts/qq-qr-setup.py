#!/usr/bin/env python3
"""QQ Bot QR-code scan-to-configure helper.

Run this script to bind a QQ bot to Hermes via QR code scan.
The app_id and client_secret are written to ~/.hermes/config.yaml automatically.
"""
import sys
import os

# Add the project root to path so we can import gateway modules
project_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(project_root))

import yaml
from pathlib import Path

from gateway.platforms.qqbot.onboard import qr_register
from hermes_cli.config import get_hermes_home


def main():
    print("=" * 50)
    print("  QQ Bot 扫码绑定")
    print("=" * 50)
    print()
    print("请在手机 QQ 中扫描终端显示的二维码")
    print("扫码后会自动获取 App ID 和 Client Secret")
    print()

    result = qr_register(timeout_seconds=600)

    if not result:
        print("❌ 扫码绑定失败或超时，请重试。")
        sys.exit(1)

    app_id = result["app_id"]
    client_secret = result["client_secret"]
    user_openid = result.get("user_openid", "")

    print()
    print(f"✅ 绑定成功！")
    print(f"   App ID: {app_id}")
    if user_openid:
        print(f"   OpenID: {user_openid}")
    print()

    # Write to config.yaml
    hermes_home = get_hermes_home()
    config_path = hermes_home / "config.yaml"

    config = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # Ensure platforms section exists
    if "platforms" not in config:
        config["platforms"] = {}

    config["platforms"]["qqbot"] = {
        "enabled": True,
        "extra": {
            "app_id": app_id,
            "client_secret": client_secret,
            "markdown_support": True,
            "dm_policy": "open",
            "group_policy": "open",
        },
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"✅ 配置已写入 {config_path}")
    print()
    print("下一步：启动 Gateway 让 QQ 机器人上线")
    print("  cd /media/liu/文件1/IDEWorkplaces/GitHub/hermes-agent")
    print("  hermes --gateway")


if __name__ == "__main__":
    main()
