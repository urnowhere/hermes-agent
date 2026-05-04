#!/usr/bin/env python3
"""i18n - Internationalization support for Hermes Agent.

Provides language detection from config and translation functions for
localizing user-facing strings.
"""

import os
from typing import Optional


def get_config_language() -> str:
    """Get the current language setting from config.
    
    Returns:
        Language code: 'zh' for Chinese, 'en' for English (default)
    """
    # Try to read from config without importing the full config module
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        return "en"
    
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        # Check approvals.language first, then display.language
        lang = config.get("approvals", {}).get("language")
        if not lang:
            lang = config.get("display", {}).get("language")
        if not lang:
            lang = config.get("language")
        
        return lang if lang in ("zh", "en") else "en"
    except Exception:
        return "en"


def is_chinese() -> bool:
    """Check if current language is Chinese."""
    return get_config_language() == "zh"


def format_zh(text: str, **kwargs) -> str:
    """Format text with Chinese translation if language is set to Chinese.
    
    Args:
        text: English text to translate
        **kwargs: Format arguments for f-string style formatting
        
    Returns:
        Translated text (Chinese if config language is 'zh', otherwise English)
        
    Example:
        >>> format_zh("Compressing {count} messages...", count=10)
        "正在压缩 10 条消息..."  # if language is zh
        "Compressing 10 messages..."  # if language is en
    """
    # Full-message translations (checked first)
    full_translations = {
        '📬 No home channel is set for {name}. A home channel is where Hermes delivers cron job results and cross-platform messages.\n\nType /sethome to make this chat your home channel, or ignore to skip.':
            '📬 {name} 未设置主频道。\n\n主频道是 Hermes 投递定时任务结果和跨平台消息的地方。\n\n输入 /sethome 将此聊天设为主频道，\n或忽略以跳过。',

        '⏳ Still working... ({elapsed} min elapsed{detail})':
            '⏳ 仍在工作中...（已运行 {elapsed} 分钟{detail})',

        'Sending after interrupt: \'{preview}\'':
            '中断后发送：\'{preview}\'',
    }

    translations = {
        # Compression feedback
        "Compressing": "正在压缩",
        "Compressed:": "已压缩:",
        "Rough transcript estimate:": "粗略转录估算:",
        
        # Session token usage
        "Session Token Usage": "会话 Token 使用",
        "Model:": "模型:",
        "Input tokens:": "输入 tokens:",
        "Output tokens:": "输出 tokens:",
        "Total tokens:": "总 tokens:",
        "Session messages:": "会话消息:",
        "Session context:": "会话上下文:",
        
        # Session list
        "Session:": "会话:",
        "Title:": "标题:",
        "Duration:": "时长:",
        "Messages:": "消息:",
        
        # Voice mode
        "Voice mode enabled": "语音模式已启用",
        "Voice mode disabled.": "语音模式已禁用。",
        "Voice mode is already enabled.": "语音模式已启用。",
        "Voice mode unavailable in this environment:": "语音模式在此环境中不可用：",
        "Voice mode requirements not met:": "语音模式要求未满足：",
        "Voice TTS": "语音 TTS",
        "Voice Mode Status": "语音模式状态",
        "Mode:": "模式:",
        "TTS:": "TTS:",
        "Recording:": "录音:",
        "Record key:": "录音键:",
        "Requirements:": "要求:",
        
        # Clarify timeout
        "clarify timed out after": "澄清超时，超时时间：",
        " — agent will decide)": "— 将由代理自行决定",
        
        # Approval timeout
        "Timeout — denying command": "超时 — 拒绝命令",
        
        # Approval choices
        "once": "仅一次",
        "session": "会话",
        "always": "始终",
        "deny": "拒绝",
        "view": "查看",
        "Allow once": "允许仅一次",
        "Allow for this session": "允许本次会话",
        "Add to permanent allowlist": "添加到永久白名单",
        "Deny": "拒绝",
        
        # Dangerous Command UI
        "Dangerous Command": "危险命令",
        "Show full command": "显示完整命令",
        
        # MCP reload
        "Reloading MCP servers...": "正在重新加载 MCP 服务器...",
        
        # Snapshot
        "No state snapshots yet.": "暂无状态快照。",
        "Create one:": "创建快照:",
        
        # Session not found
        "Session not found:": "会话未找到:",
        
        # Voice TTS status
        "Voice TTS enabled.": "语音 TTS 已启用。",
        "Voice TTS disabled.": "语音 TTS 已禁用。",
        
        # General
        "messages": "消息",
        "messages...": "消息...",

        # Gateway notifications
        "Still working...": "仍在工作中...",
        "min elapsed": "分钟已过",
        "iteration": "迭代",
        "running:": "运行中:",
        "waiting for non-streaming API response": "等待非流式 API 响应",
        "waiting for provider response (streaming)": "等待供应商响应（流式）",
        "waiting for stream response": "等待流式响应",
        "s, no chunks yet": "秒，尚无数据块",
        "starting new turn (cached)": "开始新轮次（缓存）",
        "initializing": "初始化中",

        # Home channel
        "No home channel is set for": "{0} 未设置主频道",
        "A home channel is where Hermes delivers cron job results": "主频道是 Hermes 投递定时任务结果的位置",
        "Type /sethome to make this chat your home channel,": "输入 /sethome 将此聊天设为主频道，",
        "or ignore to skip.": "或忽略以跳过。",

        # Banner
        "Session:": "会话:",

        # Interrupt messages
        "New message detected, interrupting...": "检测到新消息，正在中断...",
        "Sending after interrupt:": "中断后发送：",
        "Interrupting agent... (press Ctrl+C again to force exit)": "正在中断 agent...（再按 Ctrl+C 强制退出）",
        "Interrupted during API call.": "API 调用过程中被中断",
        "Interrupt requested": "请求中断",
        "Force exiting...": "强制退出...",
        "Interrupt: skipping": "中断：跳过",
        "Breaking out of tool loop due to interrupt...": "因中断跳出工具循环...",
        "Interrupt detected during retry wait, aborting.": "重试等待阶段检测到中断，已中止",
        "Interrupt detected during error handling, aborting retries.": "错误处理阶段检测到中断，已中止重试",
        "Suspend (Ctrl+Z) is not supported on Windows.": "Windows 系统不支持挂起操作（Ctrl+Z）",
        "Starting Hermes Gateway (messaging platforms)...": "正在启动 Hermes 网关（消息平台）...",
        "Starting conversation:": "开始对话：",
        "Error generating insights:": "生成洞察时出错：",
        "Error loading gateway config:": "加载网关配置时出错：",
        "Warning: Unknown toolsets:": "警告：未知的工具集：",
        "Warning: No TTS provider available. Install edge-tts or set API keys.": "警告：无可用的 TTS 服务提供商。安装 edge-tts 或设置 API 密钥。",
        "Loading skill:": "正在加载技能：",
        "Stopping": "正在停止",
        "background process(es)...": "后台进程...",
    }
    
    if not is_chinese():
        # English - just format the text
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError):
                return text
        return text
    
    # Chinese - translate and format
    # 1. Check full-message translations first
    if text in full_translations:
        result = full_translations[text]
        if kwargs:
            try:
                return result.format(**kwargs)
            except (KeyError, ValueError):
                return result
        return result

    # 2. Fall back to substring replacements for known phrases
    result = text
    for en, zh in translations.items():
        if en in result:
            result = result.replace(en, zh)
    
    # Then apply format arguments
    if kwargs:
        try:
            result = result.format(**kwargs)
        except (KeyError, ValueError):
            pass
    
    return result
