"""
HermesAgent for BIRD (text-to-SQL) evaluation.

Given a natural language question and a database schema, the agent produces
a SQL query. Evaluation is execution accuracy (EX): the predicted SQL's
result set must match the ground truth SQL's result set.

The agent uses a run_sql tool so it can iteratively explore the database
and refine its query — rather than single-shot generation.

Usage:
    python environments/benchmarks/bird/run_eval.py \\
        --model anthropic/claude-sonnet-4-5 \\
        --base-url openrouter \\
        --db-root-path /path/to/dev_databases \\
        --questions-path /path/to/dev.json
"""

import asyncio
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert SQL analyst. Given a database schema and a natural language question, write a SQLite query that answers the question.

You have access to a `run_sql` tool to execute queries against the database and inspect results. Use it to:
- Explore table contents and verify your understanding of the schema
- Test and refine your query before submitting your final answer

When you are confident in your answer, call the `finish` tool with your final SQL query."""

RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Execute a SQL query against the database and return the results.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query to execute."}
            },
            "required": ["sql"],
        },
    },
}

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Submit your final SQL answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The final SQL query that answers the question."}
            },
            "required": ["sql"],
        },
    },
}


def generate_schema_prompt(db_path: str, num_rows: int = 3) -> str:
    """Build a CREATE TABLE schema prompt with optional sample rows."""
    parts = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    for (table_name,) in tables:
        if table_name == "sqlite_sequence":
            continue
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        )
        row = cursor.fetchone()
        if not row:
            continue
        create_sql = row[0]
        if num_rows:
            safe_name = f"`{table_name}`" if table_name.lower() in ("order", "by", "group") else table_name
            try:
                cursor.execute(f"SELECT * FROM {safe_name} LIMIT {num_rows}")
                cols = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
                sample = f"/* Sample rows:\n{', '.join(cols)}\n"
                sample += "\n".join(", ".join(str(v) for v in r) for r in rows)
                sample += " */"
                parts.append(create_sql + "\n" + sample)
            except Exception:
                parts.append(create_sql)
        else:
            parts.append(create_sql)
    conn.close()
    return "\n\n".join(parts)


def execute_sql(sql: str, db_path: str, timeout: float = 10.0) -> str:
    """Execute SQL and return results as a string, with timeout."""
    import concurrent.futures

    def _run():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchmany(50)
        cols = [d[0] for d in cursor.description] if cursor.description else []
        conn.close()
        if not rows:
            return "No results."
        header = " | ".join(cols)
        body = "\n".join(" | ".join(str(v) for v in r) for r in rows)
        return f"{header}\n{body}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_run)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return "Error: query timed out."
        except Exception as e:
            return f"Error: {e}"


def extract_sql_from_text(text: str) -> Optional[str]:
    """Extract a SQL query from plain text as a fallback when finish tool isn't called."""
    # Try ```sql ... ``` block
    m = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Try ``` ... ``` block
    m = re.search(r"```\s*(SELECT.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Last SELECT in the text
    m = re.search(r"(SELECT\s+.+?)(?:;|$)", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None                                                                                                                                                          

class BirdHermesAgent:
    """
    BIRD text-to-SQL agent using atroposlib's OpenAIServer.

    The agent is given a schema prompt + question and uses a run_sql tool
    to iteratively explore the database before calling finish() with its
    final SQL answer.
    """

    def __init__(
        self,
        server,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        max_turns: int = 10,
        num_sample_rows: int = 3,
    ):
        self.server = server
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body
        self.max_turns = max_turns
        self.num_sample_rows = num_sample_rows

    def predict(self, question: str, db_path: str, evidence: str = "") -> str:
        """Synchronous entry point. Returns the predicted SQL string."""
        return asyncio.run(self._predict_async(question, db_path, evidence))

    async def _predict_async(self, question: str, db_path: str, evidence: str) -> str:
        schema = generate_schema_prompt(db_path, num_rows=self.num_sample_rows)

        user_content = f"Database schema:\n{schema}\n\n"
        if evidence:
            user_content += f"External knowledge: {evidence}\n\n"
        user_content += f"Question: {question}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        final_sql: Optional[str] = None

        for _ in range(self.max_turns):
            chat_kwargs: Dict[str, Any] = {
                "messages": messages,
                "n": 1,
                "temperature": self.temperature,
                "tools": [RUN_SQL_TOOL, FINISH_TOOL],
            }
            if self.max_tokens is not None:
                chat_kwargs["max_tokens"] = self.max_tokens
            if self.extra_body:
                chat_kwargs["extra_body"] = self.extra_body

            try:
                response = await self.server.chat_completion(**chat_kwargs)
            except Exception as e:
                logger.error("chat_completion failed: %s", e)
                break

            if not response or not response.choices:
                break

            msg = response.choices[0].message

            if hasattr(msg, "model_dump"):
                assistant_msg = msg.model_dump()
                if assistant_msg.get("content") is None:
                    assistant_msg["content"] = ""
            else:
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": _normalize_tool_calls(getattr(msg, "tool_calls", None)),
                }

            messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                # Model responded without a tool call — try to extract SQL from text
                final_sql = extract_sql_from_text(assistant_msg.get("content", ""))
                break

            # Process the first tool call
            tc = tool_calls[0]
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])
            tc_id = tc.get("id", "call_0")

            if tool_name == "finish":
                final_sql = tool_args.get("sql", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "Done.",
                })
                break
            elif tool_name == "run_sql":
                result = execute_sql(tool_args.get("sql", ""), db_path)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })
            else:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": f"Unknown tool: {tool_name}",
                })

        if hasattr(self.server, "openai") and hasattr(self.server.openai, "close"):
            await self.server.openai.close()

        return final_sql or "SELECT 1"


def _normalize_tool_calls(tool_calls) -> List[Dict[str, Any]]:
    if not tool_calls:
        return []
    result = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            result.append({
                "id": tc.get("id", "call_0"),
                "type": "function",
                "function": {
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", "{}"),
                },
            })
        else:
            result.append({
                "id": getattr(tc, "id", "call_0"),
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })
    return result
