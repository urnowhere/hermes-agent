# 在本地使用 Hermes + Formsy 运行 SWE-bench

本文记录如何在本地通过 `mini-swe-agent` 触发 SWE-bench 任务的编译阶段，并在后续交互式调试中让 Hermes 使用 Formsy 的上下文检索能力。

## 目标

这个流程主要用于：

- 先通过 SWE-bench runner 完成目标仓库的环境准备和 memory compile。
- 中断自动执行流程，避免让 runner 直接跑完整个任务。
- 复用 runner 生成的 prompt，在 Hermes 中进行交互式调试。
- 在 Hermes 中通过 `context_search` 和 `context_read` 查询 Formsy 已编译的代码上下文。

## 前置条件

确保本地已经准备好：

- Hermes checkout 和可用配置。
- Formsy server 项目。
- `mini-swe-agent` 项目及其 Python 虚拟环境。
- SWE-bench 数据集、目标 repo 和目标 env。

下面示例使用的 case 是 `django__django-11400`，实际运行时需要替换为你要测试的 instance。

## 1. 配置 Hermes 使用 Formsy context engine

修改 Hermes 配置文件：

```bash
vim ~/.hermes/config.yaml
```

加入或调整如下配置：

```yaml
context:
  engine: formsy

formsy:
  base_url: http://localhost:8000
  memory_search_endpoint: /api/v1/query
  workspace_id: ws_default
  timeout_s: 30
  max_retries: 3
```

注意：`context.engine` 只能配置一个值。

- 需要启用 Formsy 时，设置为 `formsy`。
- 需要测试 Hermes 默认行为时，设置为 `compressor`。

## 2. 启动 Formsy server

进入 Formsy 项目目录并启动服务：

```bash
cd path/to/formsy

uv run --package formsy-server python -m formsy.server.cli \
  --host 0.0.0.0 \
  --port 8000
```

启动后，Hermes 会通过 `http://localhost:8000` 访问 Formsy。

## 3. 运行 SWE-bench 到 memory compile 完成

进入 `mini-swe-agent` 项目并激活虚拟环境：

```bash
cd path/to/mini-swe-agent
source venv/bin/activate
```

运行单个 SWE-bench instance：

```bash
python scripts/run_swebench_single_local_python.py \
  --dataset /Users/xx/software/mini-swe-agent/evals/dataset/swebench_verified_test_django_cases.jsonl \
  --instance django__django-11400 \
  --repo-dir ./runs/data/repos/django__django \
  --env-dir ./runs/data/envs/django__django \
  --extra-config swebench-memory-query.yaml \
  --extra-config memory.enabled=true \
  --extra-config memory.base_url=http://localhost:8000 \
  --extra-config memory.timeout_seconds=180 \
  --extra-config memory.query_budget=4000
```

这个步骤不需要完整跑完。目标是完成第一个 step 中的代码 compile / memory compile。后续交互式调试时，Hermes 会调用 Formsy 进行 memory query。

日志会类似如下：

```text
👋 This is mini-swe-agent version 2.2.8.
Check the v2 migration guide at https://klieret.short.gy/mini-v2-migration
Loading global config from '/Users/xx/Library/Application Support/mini-swe-agent/.env'
minisweagent: INFO: Loading dataset from /Users/xx/software/mini-swe-agent/evals/dataset/swebench_verified_test_django_cases.jsonl, split train...
minisweagent: INFO: Building agent config from specs: [
  '/Users/xx/software/mini-swe-agent/src/minisweagent/config/benchmarks/swebench.yaml',
  'environment.cwd=/Users/xx/software/mini-swe-agent/runs/data/repos/django__django',
  'environment.env.VIRTUAL_ENV=/Users/xx/software/mini-swe-agent/runs/data/envs/django__django',
  'environment.env.PATH=/Users/xx/software/mini-swe-agent/runs/data/envs/django__django/bin:/usr/bin:/bin:/usr/sbin:/sbin',
  'swebench-memory-query.yaml',
  'memory.enabled=true',
  'memory.base_url=http://localhost:8000',
  'memory.timeout_seconds=180',
  'memory.query_budget=4000'
]
minisweagent: INFO: Step   1 ($0.00)
minisweagent: INFO: Step   2 ($0.00)
minisweagent: INFO: Step   3 ($0.00)
minisweagent: INFO: Step   4 ($0.00)
```

当 Step 1 完成后，可以先按 `Ctrl+C` 中断 runner。

## 4. 提取 Hermes 使用的 prompt

runner 中断后，可以从 `mini-swe-agent` 的 trajectory 文件中找到 Hermes 使用的 prompt 模板：

```bash
cat ~/Library/Application\ Support/mini-swe-agent/last_swebench_single_run.traj.json
```

在 trajectory 中找到类似下面的 messages：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful assistant that can interact with a computer shell to solve programming tasks."
    },
    {
      "role": "user",
      "content": "<pr_description>...</pr_description>\n\n<instructions>...</instructions>"
    }
  ]
}
```

后续可以把 system prompt 和 user prompt 拼成一段，直接发给 Hermes 进行交互式调试。

## 5. 准备 Hermes 交互式调试 prompt

使用上一步提取的 prompt，并追加 memory search guidance。注意把 `repo_id` 修改成当前 SWE-bench case ID，例如 `django__django-11400`。

示例 prompt：

```text
You are a helpful assistant that can interact with a computer shell to solve programming tasks.

<pr_description>
...
</pr_description>

<instructions>
...
</instructions>

## Memory Search Guidance

- repo_id: `django__django-11400`
- revision: `latest`

Use `context_search` proactively and repeatedly. Prefer narrow queries with an explicit `intent`.

Available search intents:

- `symbol_definition`: find definitions of classes, functions, methods, constants
- `file`: find likely files or modules
- `call_flow`: find callers, callees, and how values flow between methods
- `behavior`: find code related to the PR-described behavior
- `tests`: find relevant regression tests or existing behavioral tests
- `regression`: find edge cases and compatibility risks
- `general`: fallback when no specific intent applies

Good `context_search` calls:

- `{"intent": "symbol_definition", "query": "target_symbol", "repo_id": "...", "revision": "latest"}`
- `{"intent": "file", "query": "django/contrib/staticfiles/storage.py staticfiles storage", "repo_id": "...", "revision": "latest"}`
- `{"intent": "behavior", "query": "post_process yields duplicate filenames collectstatic", "repo_id": "...", "revision": "latest"}`
- `{"intent": "call_flow", "query": "collectstatic collect consumes post_process yielded hashed_name processed", "repo_id": "...", "revision": "latest"}`
- `{"intent": "tests", "query": "staticfiles tests post_process collectstatic hashed files duplicate yield final hash", "repo_id": "...", "revision": "latest"}`
- `{"intent": "regression", "query": "ManifestStaticFilesStorage nested references multiple passes intermediate files", "repo_id": "...", "revision": "latest"}`

After `context_search` returns `matches`, use `context_read` to inspect exact source ranges before editing:

- `{"path": "django/contrib/staticfiles/storage.py", "start_line": 203, "end_line": 330, "repo_id": "...", "revision": "latest"}`

Do not worry about compiling or pre-indexing memory. The memory compile step has already been completed before this task starts, so `context_search` and `context_read` are ready to use immediately.
```

## 6. 启动 Hermes 并保存会话

进入 SWE-bench runner 使用的目标仓库目录。以 `django__django-11400` 为例：

```bash
cd /Users/xx/software/mini-swe-agent/runs/data/repos/django__django
```

在该目录启动 Hermes：

```bash
hermes
```

将第 5 步准备好的 prompt 粘贴到 Hermes 中开始交互式调试。任务完成后，在 Hermes 中输入：

```text
/save
```

这样可以保存当前会话，后续需要复盘或继续调试时可以恢复上下文。

## 常见注意事项

- `context.engine` 在同一时间只能选择一个 engine。切换 Formsy 和默认 compressor 行为时，需要修改配置。
- `repo_id` 必须和当前 SWE-bench instance 对齐，否则 Hermes 可能查询不到正确上下文。
- Formsy server 必须在 Hermes 调用 `context_search` 前保持运行。
- Step 1 完成后即可中断 runner；不需要等待 mini-swe-agent 完整解决任务。
- 启动 Hermes 前要先 `cd` 到目标 repo 目录，否则工具调用和文件编辑会发生在错误的工作目录。
