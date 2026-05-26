# 在本地使用 Hermes + Formsy 运行 SWE-bench

## 1. 准备数据集和仓库

确保已下载 SWE-bench 数据集（JSONL 格式）和对应的 repo 仓库。

数据集示例路径：
- `data/ScaleAI_SWE-bench_Pro/data/swe-bench-pro-python-ansible.jsonl`

Repo 示例路径：
- `/Users/xx/software/mini-swe-agent/runs/data/repos/django__django`

## 2. 使用脚本设置 Repo 并生成 Prompt

使用 `scripts/generate_swebench_prompt.py` 一键完成 repo 重置和 prompt 生成：

```bash
python scripts/generate_swebench_prompt.py \
  --dataset data/ScaleAI_SWE-bench_Pro/data/swe-bench-pro-python-ansible.jsonl \
  --instance-id instance_ansible__ansible-395e5e20fab9cad517243372fa3c3c5d9e09ab2a-v7eee2454f617569fd6889f2211f75bc02a35f9f8 \
  --repo-dir /Users/xx/software/mini-swe-agent/runs/data/repos/ansible__ansible
```

脚本会自动完成以下操作：
1. 从数据集中查找指定 `instance_id`，获取 `base_commit` 和 `problem_statement`
2. 在 `--repo-dir` 中执行 `git reset --hard <base_commit>` 和 `git clean -fd`，清理所有本地变更
3. 生成包含 `problem_statement` 的完整 prompt 并输出到 stdout

## 3. 运行 SWE-bench

启动 hermes，将生成的 prompt 作为初始输入发送即可。
