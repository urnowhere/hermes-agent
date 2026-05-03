---
name: cron-job-troubleshooting
description: "Cron job常见问题排查：delivery设置、工具集配置、静默失败原因"
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cron, troubleshooting, hermes]
---

# Cron Job 故障排查

## 常见问题

### 1. 定时任务没发送结果给用户
**原因**：`deliver` 设置为 `"local"` 而不是 `"origin"`

**解决方案**：
```python
cronjob(action='update', job_id='xxx', deliver='origin')
```

`deliver` 可选值：
- `"origin"` — 发送到用户的原始渠道（WeChat/Telegram等）
- `"local"` — 仅保存到本地文件，不发送

### 2. 工具在 cron job 中不可用
**原因**：cron job 使用受限的工具集，默认只有 `hermes-cli`，不包含 `browser`、`car-scraper` 等

**解决方案**：
- 使用 cron job 兼容的工具（terminal、file、web等）
- 或通过 script 参数运行独立脚本

### 3. 静默失败返回 `[SILENT]`
**原因**：
- 缺少必要工具（browser、car-scraper 等不在工具集中）
- 任务执行时间过长导致超时或被截断
- agent 输出中包含 `[SILENT]` 字样（触发抑制逻辑）

**排查步骤**：
1. 检查 cron job 的 `last_error` 字段
2. **主要手段**：查看 `~/.hermes/sessions/session_cron_<job_id>_<timestamp>.json` — 这是完整执行记录，比 `~/.hermes/logs/agent.log` 更可靠（日志可能被轮转或截断）
3. 确认所需工具在 cron job 工具集中可用

**Session 文件命名规律**：
```
session_cron_<job_id>_<YYYYMMDD_HHMMSS>.json
```
可以用 `ls -la ~/.hermes/sessions/ | grep cron_<job_id>` 快速找到。

### 4. 任务执行失败——模型不存在（404 / Non-retryable client error）

**症状**：cron job 的 `last_status: error`，用户没收到推送。session 文件中**只有1条 user 消息**，agent 未生成任何回复。日志中有：
```
Non-retryable client error: Error code: 404 - {error: {message: 'The model \`xxx\` does not exist.'}}
```

**原因**：cron job 创建时固定了 model 名称（如 `data/code/my_models/SomeModel`），但 API 服务重启后该模型名不再可用。

**排查步骤**：
```bash
# 1. 查看 cron job 的模型配置
cronjob(action='list')  # 看 model 字段

# 2. 确认当前 API 可用的模型
curl -s <base_url>/v1/models -H "Authorization: Bearer <api_key>" 2>/dev/null

# 3. 检查 agent.log 确认错误
grep "Non-retryable client error\|model.*does not exist" ~/.hermes/logs/agent.log
```

**解决方案**：更新 cron job 的 model 为当前可用的模型名，或清空 model 以使用默认模型：
```python
# 方案A：指定一个存在的模型
cronjob(action='update', job_id='xxx', model='deepseekv4-flash', provider='custom')

# 方案B：清空 model（使用 config.yaml 中的默认模型）
# 注意：cronjob update 需要显式传 model，不能直接清空
```

**预防**：创建 cron job 时尽量不固定 model 名称，或使用稳定的模型别名。API 服务升级/重启后如模型名变化，需同步更新所有 cron job 的 model 字段。

### 5. 定时任务没有运行
**检查命令**：
```bash
cronjob(action='list')
```

确认：
- `enabled: true`
- `state: "scheduled"`
- `next_run_at` 时间正确

### 5b. 任务执行成功但微信推送失败（last_status: ok, last_delivery_error 有值）

**通用排查**：
1. 用户的最后状态检查：`cronjob(action='list')` → 看 `last_status` 和 `last_delivery_error`
2. 查看 cron 输出（不会被 delivery 失败影响）：`ls -la ~/.hermes/cron/output/<job_id>/ && cat ~/.hermes/cron/output/<job_id>/<timestamp>`
3. 查看完整 session 日志：`ls -la ~/.hermes/sessions/ | grep cron_<job_id>` → grep session JSON
4. 查看 agent.log：`grep "delivery\|weixin\|send failed\|ret=\|error" ~/.hermes/logs/agent.log | tail -20`

#### 类型 A：aiohttp Timeout

**日志特征**：
```
Timeout context manager should be used inside a task
Weixin send failed: Timeout context manager should be used inside a task
```

**原因**：`aiohttp 3.13.x` 的 `ClientSession.__aenter__` 在进入时调用 `asyncio.timeout`（`BaseTimerContext`），要求当前线程有运行中的 asyncio task。但 cron delivery 通过 `asyncio.run_coroutine_threadsafe` 调用 live adapter 时，协程虽然被调度成 task，却没有 "running task" context（task 运行在 ThreadPoolExecutor 线程，而非主线程）。

**临时修复**：把 `deliver` 改为 `"local"`，阻止微信推送：
```python
cronjob(action='update', job_id='xxx', deliver='local')
```

**长期修复**：需修改 Hermes Agent 代码，让 WeChat 推送不走 `run_coroutine_threadsafe` 路径，而是用独立事件循环或修复 aiohttp 调用方式。

#### 类型 B：iLink ret=-2 unknown error

**日志特征**：
```
WARNING [Weixin] ret=-2 unknown error; retrying without context_token
WARNING [Weixin] send chunk failed attempt=2/3, retrying in 2.00s: iLink sendmessage error: ret=-2 errcode=None errmsg=unknown error
ERROR   [Weixin] send failed: iLink sendmessage error: ret=-2 errcode=None errmsg=unknown error
```

**原因**：微信桥接接口（iLink）的临时性通信错误。前几次 retry 会尝试 `send with context_token` → 失败后 fallback 到 `send without context_token` → 同样失败。属于微信桥端的瞬时故障（连接抖动、超时或 token 过期），非 agent 本身问题。

**特征**：
- `last_status: ok`（任务执行成功）
- `last_delivery_error` 里有 `ret=-2`
- 通常是**偶发性的**，后续自动恢复
- 数据仍然可以通过 cron output 查看：`cat ~/.hermes/cron/output/<job_id>/<timestamp>.md`

**处理方法**：
1. **不要手动重启 gateway** — 发送失败不会影响后续消息，gateway 连接通常会自动恢复
2. **检查 gateway 是否在线**：看 agent.log 确认 `✓ weixin connected`
3. **数据恢复**：通过 cron output 读取已生成的报告，在用户询问时直接提供内容
4. **如果频繁出现**（连续 3+ 天），考虑：更换交付方式（如 deliver='local' + 定时推摘要），或排查 iLink 稳定性

### 5. 手动触发测试
```python
cronjob(action='run', job_id='xxx')
```

**注意**：`cronjob(action='run', ...)` 不会创建新的 cron session，而是在**当前会话**中同步执行。真正的 cron session 文件名格式为 `session_cron_<job_id>_<timestamp>.json`，如果看不到新的这类文件，说明 run 在当前会话中执行了。

## 6. 建立自动健康检查 & 自愈机制

当用户提出"检查cron是否正常运行"或"自动修复失败的cron"时，使用此模式。

### 架构

```
cron_health_collector.py (Python helper, 纯文件系统操作)
        ↓
Cron Job Agent (Hermes cron job, 每天18:00)
   ├── cronjob(action='list') → 检查 last_status + last_delivery_error
   ├── terminal + send_message → 修复失败的任务
   └── 生成健康报告发给用户
```

### 创建步骤

#### A. 创建数据收集脚本 `~/.hermes/scripts/cron_health_collector.py`

一个纯 Python 脚本（不依赖 Hermes 工具），检查以下数据源并输出 JSON：
1. `~/.hermes/cron/output/<job_id>/` — 每个 job 最新的输出文件和生成时间
2. `~/.hermes/logs/agent.log` — grep `delivery error|send failed|last_delivery_error` 获取最近推送错误

参考代码（`~/.hermes/scripts/cron_health_collector.py`）：

```python
#!/usr/bin/env python3
import json, os, re, glob, subprocess
from datetime import datetime, timezone
CRON_OUTPUT_BASE = os.path.expanduser("~/.hermes/cron/output")
AGENT_LOG = os.path.expanduser("~/.hermes/logs/agent.log")
def collect():
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "jobs": {}, "recent_delivery_errors": []}
    if os.path.isdir(CRON_OUTPUT_BASE):
        for job_dir in sorted(os.listdir(CRON_OUTPUT_BASE)):
            job_path = os.path.join(CRON_OUTPUT_BASE, job_dir)
            if not os.path.isdir(job_path): continue
            all_files = sorted(glob.glob(os.path.join(job_path, "*.md")) + glob.glob(os.path.join(job_path, "*.json")), reverse=True)
            info = {"job_id": job_dir, "total_outputs": len(all_files), "latest": None}
            if all_files:
                info["latest"] = os.path.basename(all_files[0])
                m = re.match(r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})', info["latest"])
                if m: info["latest_timestamp"] = f"{m.group(1)}T{m.group(2).replace('-', ':')}"
                try:
                    with open(all_files[0], 'r', encoding='utf-8', errors='replace') as f:
                        info["preview"] = f.read(2000)
                except: info["preview"] = None
            report["jobs"][job_dir] = info
    if os.path.isfile(AGENT_LOG):
        try:
            r = subprocess.run(["grep", "-E", "delivery error|send failed|last_delivery_error|delivery_error", AGENT_LOG], capture_output=True, text=True, timeout=10)
            report["recent_delivery_errors"] = [l.strip() for l in r.stdout.split("\n") if l.strip()][-30:]
        except: pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
if __name__ == "__main__": collect()
```

#### B. 创建健康检查 Cron Job

```python
cronjob(action='create',
    name='Cron 健康检查 & 自动恢复',
    schedule='0 18 * * *',  # 每天下午6点
    deliver='origin',
    prompt='''你是 Cron 健康检查 & 自动恢复 agent。

## 步骤

### 1. 收集状态
先运行数据收集器：
```bash
cd /home/jianliu/work/hermes-agent && python ~/.hermes/scripts/cron_health_collector.py
```
然后 cronjob(action='list') 获取每个 job 的 last_status 和 last_delivery_error。

### 2. 逐个诊断修复

- **执行失败** (last_status != 'ok') → cronjob(action='run', job_id='xxx') 重新执行
- **推送失败** (last_status='ok' 但 last_delivery_error 非空):
  1. 从 ~/.hermes/cron/output/<job_id>/ 找到最新输出文件
  2. terminal('cat <file_path>') 读取内容
  3. send_message(target='weixin', message='...') 重新发送
- **正常** → 跳过

### 3. 输出报告

```markdown
🔍 Cron 健康检查报告
━━━━━━━━━━━━━━━━━━━
📅 检查时间：...
✅ 正常运行的 job：...
🔄 已自动恢复：...
❌ 仍失败：...
📊 汇总：x个正常，x个恢复，x个仍失败
```''')
```

### 恢复逻辑详解

| 故障类型 | last_status | last_delivery_error | 恢复操作 |
|----------|-------------|---------------------|----------|
| 执行失败 | error | — | cronjob(action='run') 重跑 |
| 推送失败 | ok | 有值 | 读 cached output → send_message |
| 同时失败 | error | 有值 | 先重跑，推送会自动恢复 |
| 正常 | ok | 空 | 无需操作 |

### 注意事项
- enabled_toolsets 必须包含 `terminal`、`web`、`search` 才能运行 python 脚本和发送消息
- 内容太长时需要截断再发微信（微信消息有长度限制）
- 这个 cron job 本身不需要复杂的 model，可以留空使用默认

### 已部署实例参考

**Job ID:** `5aea5a03c148`
**Schedule:** `0 18 * * *` (每天北京时间 18:00)
**Script:** `~/.hermes/scripts/cron_health_collector.py`
**Run:** `cd /home/jianliu/work/hermes-agent && python ~/.hermes/scripts/cron_health_collector.py`

`last_delivery_error` 常见错误模式：
- `ret=-2` = iLink 瞬时连接抖动（偶发，自动恢复）
- `ret=-1` = 超时或 token 过期（需要重启 gateway）
- `no such column` = 输出 DB schema 不兼容（需要重建 DB 或修改脚本）

### 手动恢复流程（当健康检查 cron 未及时运行时）

1. 获取状态：
```bash
cd /home/jianliu/work/hermes-agent && python ~/.hermes/scripts/cron_health_collector.py
cronjob(action='list')
```

2. 对每个有 `last_delivery_error` 的 job，读取缓存输出并重新发送：
```bash
cat ~/.hermes/cron/output/<job_id>/<latest>.md
# 然后用 send_message 重新发送
```
```bash
ls -la ~/.hermes/cron/output/<job_id>/
cat ~/.hermes/cron/output/<job_id>/<timestamp>.md
```

## 查看 cron session
```bash
ls -la ~/.hermes/sessions/ | grep cron
```

## 快速修复命令
```python
# 修复 delivery
cronjob(action='update', job_id='xxx', deliver='origin')

# 暂停任务
cronjob(action='pause', job_id='xxx')

# 恢复任务
cronjob(action='resume', job_id='xxx')

# 立即运行
cronjob(action='run', job_id='xxx')
```