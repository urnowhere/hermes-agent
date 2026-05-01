#!/usr/bin/env python3
"""
Hermes Chat 页面功能测试 - 不依赖浏览器的轻量级测试
使用 httpx 测试 API 端点和 HTML 内容
"""
import asyncio
import httpx
from pathlib import Path

BASE_URL = "http://localhost:9119"

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def check_result(name: str, passed: bool, detail: str = ""):
    status = f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"
    print(f"  {status} {name}")
    if detail and not passed:
        print(f"      {RED}详情：{detail}{RESET}")
    return passed


async def test_chat_page():
    """测试 Chat 页面"""
    print("\n=== Chat 页面测试 ===\n")

    async with httpx.AsyncClient(timeout=10.0) as client:
        passed = 0
        failed = 0

        # 测试 1: 页面能正常加载
        print("1. 测试页面加载...")
        try:
            resp = await client.get(f"{BASE_URL}/chat")
            ok = resp.status_code == 200 and "Hermes" in resp.text
            if check_result("HTTP 200 且包含 Hermes 标题", ok):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            check_result("页面加载", False, str(e))
            failed += 1

        # 测试 2: 检查 JS 文件引用
        print("2. 检查 JavaScript 文件引用...")
        required_scripts = [
            'static/i18n.js',
            'static/icons.js',
            'static/ui.js',
            'static/workspace.js',
            'static/boot.js',
        ]
        all_found = all(s in resp.text for s in required_scripts)
        if check_result(f"包含所有必需的 JS 文件 ({len(required_scripts)} 个)", all_found):
            passed += 1
        else:
            failed += 1

        # 测试 3: 检查会话令牌注入
        print("3. 检查会话令牌...")
        has_token = '__HERMES_SESSION_TOKEN__' in resp.text
        if check_result("页面包含会话令牌", has_token):
            passed += 1
        else:
            failed += 1

        # 测试 4: 检查主要 UI 元素
        print("4. 检查 UI 元素...")
        required_elements = [
            'id="panelChat"',
            'id="msg"',
            'id="btnNewChat"',
            'id="sessionList"',
        ]
        all_found = all(e in resp.text for e in required_elements)
        if check_result(f"包含主要 UI 元素", all_found):
            passed += 1
        else:
            failed += 1

        # 测试 5: 静态文件能正常访问
        print("5. 测试静态文件...")
        static_files = [
            ('/chat/static/style.css', 'text/css'),
            ('/chat/static/i18n.js', 'text/javascript'),
            ('/chat/static/boot.js', 'text/javascript'),
        ]
        static_ok = True
        for path, expected_type in static_files:
            try:
                r = await client.get(f"{BASE_URL}{path}")
                ok = r.status_code == 200 and expected_type in r.headers.get('content-type', '')
                if not ok:
                    static_ok = False
                    print(f"      {RED}✗ {path} 失败{RESET}")
            except Exception as e:
                static_ok = False
                print(f"      {RED}✗ {path}: {e}{RESET}")
        if check_result(f"所有静态文件可访问 ({len(static_files)} 个)", static_ok):
            passed += 1
        else:
            failed += 1

        # 测试 6: API 端点测试
        print("6. 测试 API 端点...")
        api_endpoints = [
            ('/api/status', 'GET'),
            ('/api/chat/sessions', 'GET'),
            ('/api/profile/active', 'GET'),
            ('/api/settings', 'GET'),
            ('/api/workspaces', 'GET'),
            ('/api/models', 'GET'),
            ('/api/onboarding/status', 'GET'),
            ('/api/projects', 'GET'),
        ]
        api_ok = True
        for endpoint, method in api_endpoints:
            try:
                r = await client.get(f"{BASE_URL}{endpoint}")
                ok = r.status_code == 200 and r.json()
                if not ok:
                    api_ok = False
                    print(f"      {YELLOW}⚠ {endpoint}: {r.status_code}{RESET}")
            except Exception as e:
                api_ok = False
                print(f"      {RED}✗ {endpoint}: {e}{RESET}")
        if check_result(f"API 端点可访问 ({len(api_endpoints)} 个)", api_ok):
            passed += 1
        else:
            failed += 1

    print(f"\n=== 测试结果：{GREEN}{passed} 通过{RESET}, {RED}{failed} 失败{RESET} ===\n")
    return failed == 0


async def test_session_creation():
    """测试会话创建功能"""
    print("=== 会话创建测试 ===\n")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 获取令牌
        resp = await client.get(f"{BASE_URL}/chat")
        token_match = resp.text.split('__HERMES_SESSION_TOKEN__="')[1].split('"')[0] if '__HERMES_SESSION_TOKEN__' in resp.text else None

        if not token_match:
            print(f"  {RED}✗ 无法获取会话令牌{RESET}")
            return False

        headers = {"Authorization": f"Bearer {token_match}"}

        # 创建新会话
        print("1. 创建新会话...")
        try:
            resp = await client.post(
                f"{BASE_URL}/api/session/new",
                headers=headers,
                json={}
            )
            ok = resp.status_code == 200
            data = resp.json()
            has_session_id = 'session_id' in data

            if check_result("创建会话成功", ok and has_session_id):
                print(f"      会话 ID: {data.get('session_id', 'N/A')}")
                return True
            else:
                print(f"      {RED}响应：{data}{RESET}")
                return False
        except Exception as e:
            check_result("创建会话", False, str(e))
            return False


async def main():
    print(f"\n{GREEN}╔════════════════════════════════════════╗╗")
    print(f"║  Hermes Chat 页面功能测试            ║")
    print(f"║  服务器：{BASE_URL}                    ║")
    print(f"╚════════════════════════════════════════╝{RESET}\n")

    # 检查服务器是否运行
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.get(f"{BASE_URL}/api/status")
    except Exception:
        print(f"{RED}错误：服务器未响应，请先启动服务器{RESET}")
        print(f"  命令：.venv/bin/uvicorn hermes_cli.web_server:app --host 0.0.0.0 --port 9119")
        return 1

    # 运行测试
    page_ok = await test_chat_page()
    session_ok = await test_session_creation()

    # 总结
    if page_ok and session_ok:
        print(f"{GREEN}所有测试通过！Chat 页面功能正常。{RESET}\n")
        return 0
    else:
        print(f"{RED}部分测试失败，请检查上方详情。{RESET}\n")
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
