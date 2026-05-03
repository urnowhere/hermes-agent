---
name: verification-agent
description: 创建验证/评审 subagent，对主 agent 的最终输出进行独立评审和测试，不看中间步骤。如不合格则反馈要求修改。
tags: [agent-pattern, review, qa]
---

# 验证 Agent 模式

## 两种实现方式概览

Hermes Agent 提供**两种**验证机制，适用于不同场景：

| 方式 | 机制 | 适用场景 |
|------|------|----------|
| **内置验证循环** | 在 `run_agent.py` 的 `run_conversation()` 末尾自动运行 | 每次文本对话的响应质量把关（零配置） |
| **Subagent 验证** | `delegate_task()` 手动创建独立验证 Agent | 复杂任务（代码生成、数据分析等），需独立评审 |

---

## 方式一：内置验证循环（自动运行）

### 概述

代码位于 `run_agent.py`（`_verify_response()`、`_refine_response()`、`_call_llm_simple()` 方法），在 `run_conversation()` 产生最终响应后**自动静默执行**，对用户透明。

### 运行条件

验证循环在以下条件**同时满足**时触发：

1. `final_response` 不为空
2. `interrupted` 为 `False`（本轮未被用户后续消息打断）
3. `original_user_message` 是字符串类型（即纯文本请求，非多模态消息）

### 工作流程

```
最终响应 → 调用 _verify_response()（verifier 只看原始请求+最终输出）
  ├─ 通过 → 日志记录 "Verifier passed round N/5" → 返回
  └─ 失败 → 有反馈 → _refine_response() 改进 → 下一轮（最多5轮）
           无反馈 → 放过（verifier 返回空反馈）
```

- 最多 **5 轮**静默迭代
- Verifier 通过同一 LLM（`_ensure_primary_openai_client`）进行评估，**不带工具**
- Refine 同样使用同一 LLM，**不带工具**，只根据 feedback 改进文本
- 如果 refine 输出与原响应相同 → 停止（防止死循环）
- 每轮 refine 后更新历史消息中的 assistant message

### 关键行为特征

- **完全静默**：无控制台输出、无用户可见提示
- **仅写日志**：通过 `logger.info()` 记录到 `agent.log`，关键词为 `"Verifier passed round"`、`"Verifier failed round"`、`"Verifier exhausted"`、`"Refine produced identical output"`
- **错误安全**：任何异常（LLM 调用失败等）都会静默放过（返回 `(True, "")`)
- **空响应/空白响应直接通过**：`_verify_response()` 会先检查 `not response.strip() or response == "(empty)"`

### 如何验证是否生效

```bash
# 检查 agent.log 中是否有 verifier 日志
grep 'Verifier\\|VERDICT' ~/.hermes/logs/agent.log

# 预期输出示例：
# Verifier passed round 1/5 (response len=1234)
```

**⚠️ 常见误判 1：本轮被用户后续消息打断**
如果日志中没有任何 `Verifier` 记录，最常见原因是**本轮被用户后续消息打断了**（`interrupted=True`）。检查时序：如果用户在 agent 响应前发了新消息，则验证循环被跳过。这不是 bug，是设计行为。

**⚠️ 常见误判 2：quiet_mode 下日志被静默抑制**
Gateway 启动时默认使用 `quiet_mode=True`，这会在 `AIAgent.__init__()`（`run_agent.py` 第 1206-1213 行）执行以下操作：
```python
if self.quiet_mode:
    for quiet_logger in ['tools', 'run_agent', ...]:
        logging.getLogger(quiet_logger).setLevel(logging.ERROR)
```
这意味着 `run_agent` logger 的 `INFO` 和 `WARNING` 级别调用（包括 `"Verifier passed round"`、`"Verifier failed round"`、`"Turn ended"` 等）**全部被静默丢弃**，不会写入任何日志文件。

**这不是验证循环没有运行，只是日志被过滤了。** 验证循环仍然在执行，`_call_llm_simple()` 仍然调用 LLM 做评估，`_refine_response()` 仍然改进输出——只是你看不到它们的痕迹。

**如何确认：**
- 方法 A（推荐）：临时将验证循环的 `logger.info()` 改为 `logger.error()`（ERROR 级别不会被 quiet_mode 抑制），重启 gateway 后再观察 `agent.log`。这是最轻量的诊断方式。
- 方法 B：在 `gateway/run.py` 中 `agent.run_conversation()` 返回后，对比返回的 `final_response` 与 refine 前的原始响应是否不同。
- 方法 C：临时注释掉 `run_agent.py` __init__ 中 quiet_mode 对 `'run_agent'` logger 的级别设置，重启 gateway。

**修复方案（已验证有效）：**
由于 `quiet_mode=True` 将 `run_agent` logger 级别提升到了 `ERROR`，所有验证循环的 `logger.info()` 和 `logger.warning()` 调用都会被丢弃。解决方案是将验证相关的日志全部提升到 `logger.error()` 级别，并加上 `[VERIFY]` 前缀方便 grep 过滤。

```python
# Before (被 quiet_mode 静默):
logger.info("Verifier passed round %d/%d", ...)

# After (穿透 quiet_mode):
logger.error("[VERIFY] Passed round %d/%d", ...)
```

受影响的调用点（在 `run_agent.py` 的验证循环中）：
- 验证循环入口日志
- 每轮 passed/failed 结果
- refine 无变化停止
- 轮次耗尽
- `_call_llm_simple`, `_verify_response`, `_refine_response` 的异常捕获

补丁位置：`~/.hermes/patches/001-verify-agent.patch`

---

## 方式二：Subagent 验证模式（手动调用）

### 概述

**双 Agent 工作流**：主 Agent 完成任务 → 验证 Agent（verifier）**只看最终结果** → 评审/测试 → 通过 or 打回修改。

### 关键规则

1. **信息隔离** — 验证 Agent 永远不知道主 Agent 的中间步骤、推理过程、使用了哪些工具
2. **只给结果** — 传给验证 Agent 的只有：最终输出的内容（文件、文本、数据等） + 原始用户需求
3. **严格把关** — 验证 Agent 扮演"挑剔的评审员"角色，对质量负责
4. **反馈回路** — 验证不通过时，输出具体的修改意见，由主 Agent 迭代修改

## 使用场景

- 代码生成 → 验证 Agent 审查代码质量、运行测试
- 数据分析 → 验证 Agent 校验结果正确性
- 文档写作 → 验证 Agent 检查准确性和完整性
- 配置生成 → 验证 Agent 验证配置语法和一致性

## 工作流（通用模板）

### 第1步：主 Agent 完成任务

正常执行用户指令，输出最终结果。

### 第2步：调用验证 Agent

使用 `delegate_task` 创建验证 subagent，只传入：

#### context 字段，包含：
1. **用户原始需求**（原文）
2. **最终输出**（主 Agent 产出的完整结果，如代码、文件、文字等）
3. **验证要求**（e.g. "执行代码并确认运行正常"、"检查逻辑正确性"、"对照需求逐条核对"）

关键：**不传**任何中间步骤、推理过程、使用的命令、尝试过程。

#### 验证 Agent 的 toolsets
- `terminal` — 如果需要执行和测试
- `file` — 如果需要读取检查文件
- 根据任务类型可选：`web`（查证引用）、`browser`（UI 校验）

### 第3步：处理验证结果

验证 Agent 返回结果包含：
- ✅ 结论（通过/不通过）
- 📋 具体问题清单（如有）
- 🔧 修改建议（如有）
- 🔄 是否建议重新评审

如果反馈要求修改：
1. 根据反馈进行修改
2. 再次调用验证 Agent（只传新版最终结果）
3. 重复直到通过

## 验证 Agent prompt 模板

```markdown
你是一个严格的验证/评审 Agent。你的职责是对以下最终输出进行独立的质量评估。

## ⚠️ 严格规则（必须遵守）
1. 你只知道最终结果，**不知道**生成它的人用了什么步骤、工具或方法
2. 不要猜测或假设中间过程，只评审你看到的东西
3. 如果发现问题，请明确指出并给出可操作的修改建议
4. 如果一切合格，明确说"通过"

## 用户需求
<用户原始需求>

## 最终输出
<主Agent的最终产出>

## 验证要求
<具体的验证项目，如：>
- 代码是否正确？能否编译/运行？（请实际执行测试）
- 是否完整覆盖了用户需求？
- 有没有遗漏边界情况？
- 输出格式和内容质量如何？
```

## 示例调用

```python
# 验证 Agent 只传最终结果，不传中间步骤
result = await delegate_task(
    goal="作为验证 agent，对以下输出进行独立评审",
    context=f"""用户需求：{user_request}

最终输出：
{final_output}

验证要求：
1. 执行输出中的代码，测试是否能正常运行
2. 对照用户需求逐条检查是否全部覆盖
3. 检查是否有逻辑错误或遗漏
4. 如发现问题，给出具体的修改建议

注意：你不知道这个输出是如何生成的，只看结果本身。""",
    toolsets=["terminal", "file"]  # 根据实际情况调整
)
```

## 注意事项

- 验证 Agent 的 toolsets 要根据任务类型选择：代码任务需要 `terminal` 和 `file`，纯文本任务可能只需要 `file`
- 复杂的任务（涉及多文件、长时间测试）建议单独为其分配较长的超时时间
- 如果任务对时效性要求高，可以设置 `max_rounds=1` 即验证最多一轮，不通过则直接报告问题由人工决定
- 验证 Agent 的上下文不要包含主 Agent 的任何试探/中间产物，这是最重要的隔离原则
