"""
BIRD text-to-SQL evaluation runner for Hermes Agent.

Downloads: https://bird-bench.github.io/ (dev set)
Expected layout:
    --db-root-path   /path/to/dev_databases/   (contains {db_id}/{db_id}.sqlite)
    --questions-path /path/to/dev.json
    --gold-sql-path  /path/to/dev_gold.sql     (optional, derived from dev.json if absent)

Usage:
    # OpenRouter
    python environments/benchmarks/bird/run_eval.py \\
        --model anthropic/claude-sonnet-4-5 \\
        --base-url openrouter \\
        --db-root-path ~/bird/dev_databases \\
        --questions-path ~/bird/dev.json

    # Local vLLM
    python environments/benchmarks/bird/run_eval.py \\
        --model NousResearch/Hermes-3-Llama-3.1-70B \\
        --base-url http://localhost:8000/v1 \\
        --db-root-path ~/bird/dev_databases \\
        --questions-path ~/bird/dev.json

Results are saved to results/bird/ as JSON.
"""

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from atroposlib.envs.server_handling.openai_server import OpenAIServer
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.benchmarks.bird.hermes_agent import BirdHermesAgent, execute_sql

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_server(model: str, base_url: str, api_key: Optional[str]) -> OpenAIServer:
    is_openrouter = "openrouter" in base_url.lower()
    if is_openrouter:
        resolved_key = (
            api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "EMPTY")
        )
    else:
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "EMPTY")

    config = APIServerConfig(model_name=model, base_url=base_url, api_key=resolved_key)
    server = OpenAIServer(config=config)

    if is_openrouter:
        server.openai = server.openai.with_options(
            default_headers={
                "HTTP-Referer": "https://hermes-agent.nousresearch.com",
                "X-Title": "Hermes Agent",
            }
        )
    return server


def evaluate_single(predicted_sql: str, ground_truth_sql: str, db_path: str, timeout: float = 30.0) -> int:
    """Execute both SQLs and compare result sets. Returns 1 if match, 0 otherwise."""
    import concurrent.futures

    def _exec():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(predicted_sql)
        pred = cursor.fetchall()
        cursor.execute(ground_truth_sql)
        gt = cursor.fetchall()
        conn.close()
        return int(set(pred) == set(gt))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_exec)
        try:
            return future.result(timeout=timeout)
        except Exception:
            return 0


def display_metrics(results: List[Dict]) -> None:
    if not results:
        print("No results.")
        return

    total = len(results)
    correct = sum(r["correct"] for r in results)
    acc = correct / total * 100

    by_diff: Dict[str, List[int]] = {}
    for r in results:
        d = r.get("difficulty", "unknown")
        by_diff.setdefault(d, []).append(r["correct"])

    print(f"\n{'='*55}")
    print(f"BIRD Execution Accuracy ({total} questions)")
    print(f"  Overall: {acc:.2f}%  ({correct}/{total})")
    for diff in ["simple", "moderate", "challenging"]:
        if diff in by_diff:
            vals = by_diff[diff]
            print(f"  {diff:12s}: {sum(vals)/len(vals)*100:.2f}%  ({sum(vals)}/{len(vals)})")
    print(f"{'='*55}\n")


def run_eval(
    model: str,
    base_url: str,
    api_key: Optional[str],
    db_root_path: str,
    questions_path: str,
    max_concurrency: int,
    temperature: float,
    max_tokens: Optional[int],
    max_turns: int,
    num_sample_rows: int,
    task_ids: Optional[List[int]],
    start_index: int,
    end_index: int,
    log_dir: str,
    seed: int,
) -> List[Dict]:
    random.seed(seed)

    if base_url.strip().lower() == "openrouter":
        base_url = OPENROUTER_BASE_URL

    os.makedirs(log_dir, exist_ok=True)
    time_str = datetime.now().strftime("%m%d%H%M%S")
    ckpt_path = os.path.join(
        log_dir,
        f"bird-{model.split('/')[-1]}-{temperature}_{time_str}.json",
    )

    questions = json.load(open(questions_path))

    if task_ids:
        questions = [questions[i] for i in task_ids]
    else:
        end = len(questions) if end_index == -1 else min(end_index, len(questions))
        questions = questions[start_index:end]

    logger.info("Running BIRD eval: %d questions, model=%s, concurrency=%d", len(questions), model, max_concurrency)
    logger.info("Checkpoint: %s", ckpt_path)

    results: List[Dict] = []

    def _run_question(idx: int, item: Dict) -> Dict:
        db_id = item["db_id"]
        db_path = os.path.join(db_root_path, db_id, f"{db_id}.sqlite")
        question = item["question"]
        evidence = item.get("evidence", "")
        ground_truth_sql = item.get("SQL", "")
        difficulty = item.get("difficulty", "unknown")

        server = build_server(model=model, base_url=base_url, api_key=api_key)
        agent = BirdHermesAgent(
            server=server,
            temperature=temperature,
            max_tokens=max_tokens,
            max_turns=max_turns,
            num_sample_rows=num_sample_rows,
        )

        logger.info("Question %d [%s/%s]: %s", idx, db_id, difficulty, question[:80])
        try:
            predicted_sql = agent.predict(question=question, db_path=db_path, evidence=evidence)
        except Exception as e:
            logger.error("Question %d failed: %s", idx, traceback.format_exc())
            predicted_sql = "SELECT 1"

        correct = evaluate_single(predicted_sql, ground_truth_sql, db_path) if ground_truth_sql else -1

        return {
            "idx": idx,
            "db_id": db_id,
            "difficulty": difficulty,
            "question": question,
            "predicted_sql": predicted_sql,
            "ground_truth_sql": ground_truth_sql,
            "correct": correct,
        }

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = {executor.submit(_run_question, i, item): i for i, item in enumerate(questions)}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            with open(ckpt_path, "w") as f:
                json.dump(sorted(results, key=lambda r: r["idx"]), f, indent=2)

    results.sort(key=lambda r: r["idx"])
    display_metrics(results)
    logger.info("Results saved to %s", ckpt_path)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="BIRD text-to-SQL evaluation with Hermes Agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--base-url", default=None,
        help="API base URL. Use 'openrouter' as shorthand.",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--db-root-path", required=True, help="Path to dev_databases/ directory")
    parser.add_argument("--questions-path", required=True, help="Path to dev.json")
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--max-turns", type=int, default=10, help="Max tool-calling turns per question")
    parser.add_argument("--num-sample-rows", type=int, default=3, help="Sample rows to include in schema prompt")
    parser.add_argument("--task-ids", type=int, nargs="*", default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int, default=-1)
    parser.add_argument("--seed", type=int, default=10)
    parser.add_argument("--log-dir", default="results/bird")

    args = parser.parse_args()

    run_eval(
        model=args.model,
        base_url=args.base_url or "https://api.openai.com/v1",
        api_key=args.api_key,
        db_root_path=args.db_root_path,
        questions_path=args.questions_path,
        max_concurrency=args.max_concurrency,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        max_turns=args.max_turns,
        num_sample_rows=args.num_sample_rows,
        task_ids=args.task_ids,
        start_index=args.start_index,
        end_index=args.end_index,
        log_dir=args.log_dir,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
