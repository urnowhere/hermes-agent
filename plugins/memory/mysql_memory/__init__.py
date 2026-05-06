"""MySQL Memory Provider — persistent cross-session memory via MySQL.

Stores memory entries in the openclaw_memory.memory_entries table,
with source_agent isolation (hermes vs prince) so each agent only
sees its own entries.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
from pymysql.cursors import DictCursor

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MYSQL_HOST = os.environ.get("MYSQL_HOST", "172.31.16.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "OpenClaw123")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "openclaw_memory")
SOURCE_AGENT = "hermes"  # this provider is for hermes; prince uses 'prince'

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "mysql_host": MYSQL_HOST,
    "mysql_port": MYSQL_PORT,
    "mysql_user": MYSQL_USER,
    "mysql_password": MYSQL_PASSWORD,
    "mysql_database": MYSQL_DATABASE,
}


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

def _get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class MySQLMemoryProvider(MemoryProvider):
    name = "mysql_memory"

    def __init__(self):
        self._conn = None
        self._session_id = None
        self._sync_thread: Optional[threading.Thread] = None
        self._pending_writes: List[Dict] = []
        self._write_lock = threading.Lock()
        # Track staging file state to avoid unnecessary re-syncs
        self._last_staging_hash: Optional[str] = None

    # ── Availability ────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check MySQL connectivity — no network call, just env check."""
        required = [MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE]
        return all(required) and MYSQL_HOST not in ("", "unknown")

    # ── Init ────────────────────────────────────────────────────────────────

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        try:
            self._conn = _get_conn()
            logger.info("[MySQLMemory] Connected to MySQL %s:%s/%s",
                        MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE)
        except Exception as e:
            logger.error("[MySQLMemory] Failed to connect: %s", e)
            raise

        # Rebuild MEMORY.md from MySQL at session start
        self._rebuild_memory_md()

    def _rebuild_memory_md(self) -> None:
        """Regenerate MEMORY.md index file from MySQL memory_entries table.

        Writes a pure index file (no real data) that tells Hermes where to
        look in MySQL for each category of memory.
        """
        from pathlib import Path
        from hermes_constants import get_hermes_home

        mem_dir = get_hermes_home() / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        memory_md_path = mem_dir / "MEMORY.md"

        # Get category stats from MySQL
        rows = self._query("""
            SELECT category, COUNT(*) as cnt,
                   SUM(CASE WHEN shared=1 THEN 1 ELSE 0 END) as shared_cnt
            FROM memory_entries
            WHERE category != 'memory_backup'
            GROUP BY category
            ORDER BY category
        """) if self._conn else []

        # Get MySQL config for protected block
        lines = [
            "# Hermes Memory Index",
            "",
            "> **铁律：回答问题前 → 先查MEMORY.md索引 → 再查MySQL真实数据 → 禁止回答未查库的信息**",
            "",
            "---",
            "",
            "## 【受保护配置块 — DO NOT MODIFY】",
            "",
            "```",
            f"MYSQL_HOST={MYSQL_HOST}",
            f"MYSQL_USER={MYSQL_USER}",
            f"MYSQL_PASSWORD=***",
            f"MYSQL_DATABASE={MYSQL_DATABASE}",
            f"MYSQL_PORT={MYSQL_PORT}",
            "```",
            "",
            "> ⚠️ 此块为系统关键配置。连接参数从插件代码/环境变量读取。",
            "",
            "---",
            "",
            "## 【memory_entries 分类索引】",
            "",
            "| category | 数量 | shared | 说明 |",
            "|---|---|---|---|",
        ]

        for row in rows:
            cat = row.get('category', '')
            cnt = row.get('cnt', 0)
            shared = "✓" if row.get('shared_cnt', 0) > 0 else ""
            desc = ""
            if cat == "知识文章":
                desc = "lesson表，三太子知识文章"
            elif cat == "事实":
                desc = "fact表，知识碎片"
            elif cat == "规则":
                desc = "rule表，操作规则"
            elif cat == "计划":
                desc = "plan表，计划任务"
            elif cat == "用户偏好":
                desc = "preference表，用户偏好"
            elif cat == "目标":
                desc = "goal表，目标"
            elif cat == "索引":
                desc = "MEMORY.md本身索引"
            elif cat == "MySQL 配置":
                desc = "MySQL连接配置"
            lines.append(f"| {cat} | {cnt} | {shared} | {desc} |")

        lines.extend([
            "",
            "> shared=✓ 表示三太子与Hermes共享数据，双方均可查询。",
            "",
            "---",
            "",
            "## 【索引查询语法】",
            "",
            "```sql",
            "-- 按category查",
            "SELECT id, content, keywords FROM memory_entries",
            "WHERE source_agent='hermes' AND category='规则';",
            "",
            "-- 按keywords模糊查",
            "SELECT id, content FROM memory_entries",
            "WHERE source_agent='hermes' AND keywords LIKE '%安全词%';",
            "",
            "-- 查所有索引条目",
            "SELECT * FROM memory_entries WHERE source_agent='hermes' AND is_index=1;",
            "",
            "-- 查共享数据（双方均可用）",
            "SELECT * FROM memory_entries WHERE shared=1;",
            "```",
            "",
            "---",
            "",
            "## 【OpenClaw MySQL 表结构】（openclaw_memory数据库）",
            "",
            "| 表名 | 主要字段 | 用途 |",
            "|---|---|---|",
            "| fact | id, content, tags | 知识事实碎片 |",
            "| lesson | id, content, domain, tags | 三太子知识文章 |",
            "| plan | id, content, deadline, status | 计划任务 |",
            "| rule | id, content, reason | 操作规则 |",
            "| preference | id, content | 用户偏好 |",
            "| goal | id, content, status, deadline | 目标 |",
            "| chat | id, sender, content, status, created_at | 对话/任务队列 |",
            "| memory_entries | id, source_agent, shared, section, category, content, keywords, is_index, mysql_query | **统一记忆索引** |",
            "",
            "---",
            "",
            "## 【写入规则】",
            "",
            "> **新知识写入流程：**",
            "> 1. 写入 `memory_entries`（真实数据）",
            "> 2. **不**在 MEMORY.md 写真实数据内容",
            "> 3. 只在 MEMORY.md 维护索引结构（category 分类说明、查询语法）",
            "> 4. 重要更新写入 chat 表通知三太子",
            "> 5. **受保护配置块**（上方）内容任何写入操作都必须保留，不得删除或修改",
        ])

        content = '\n'.join(lines)
        memory_md_path.write_text(content, encoding='utf-8')
        logger.info("[MySQLMemory] Rebuilt MEMORY.md from MySQL (%d categories)", len(rows))

    # ── Config ─────────────────────────────────────────────────────────────

    def get_config_schema(self) -> List[Dict]:
        return [
            {
                "key": "mysql_host",
                "description": "MySQL host address",
                "default": MYSQL_HOST,
            },
            {
                "key": "mysql_port",
                "description": "MySQL port",
                "default": str(MYSQL_PORT),
            },
            {
                "key": "mysql_user",
                "description": "MySQL username",
                "default": MYSQL_USER,
            },
            {
                "key": "mysql_password",
                "description": "MySQL password",
                "secret": True,
                "env_var": "MYSQL_PASSWORD",
            },
            {
                "key": "mysql_database",
                "description": "MySQL database name",
                "default": MYSQL_DATABASE,
            },
        ]

    def save_config(self, values: Dict, hermes_home: str) -> None:
        config_path = Path(hermes_home) / "mysql_memory.json"
        config_path.write_text(json.dumps(values, indent=2))
        logger.info("[MySQLMemory] Config saved to %s", config_path)

    # ── Core read/write ─────────────────────────────────────────────────────

    def _query(self, sql: str, args: tuple = ()) -> List[Dict]:
        """Execute a SELECT and return rows as list of dicts."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        finally:
            conn.close()

    def _execute(self, sql: str, args: tuple = ()) -> int:
        """Execute INSERT/UPDATE/DELETE. Returns lastrowid or affected_rows."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.lastrowid or cur.rowcount
        finally:
            conn.close()

    def search(self, query: str, *, limit: int = 10, category: str = "") -> List[Dict]:
        """Search memory entries by keywords or content.

        Args:
            query: Free-text search (matched against keywords + content)
            limit: Max results to return
            category: Optional category filter

        Returns:
            List of matching memory entry dicts
        """
        if category:
            sql = """
                SELECT id, source_agent, section, category, content, keywords,
                       is_index, mysql_query, expires_at, created_at, updated_at
                FROM memory_entries
                WHERE source_agent = %s
                  AND section = 'memory'
                  AND category = %s
                  AND (keywords LIKE %s OR content LIKE %s)
                ORDER BY updated_at DESC
                LIMIT %s
            """
            args = (SOURCE_AGENT, category, f"%{query}%", f"%{query}%", limit)
        else:
            sql = """
                SELECT id, source_agent, section, category, content, keywords,
                       is_index, mysql_query, expires_at, created_at, updated_at
                FROM memory_entries
                WHERE source_agent = %s
                  AND section = 'memory'
                  AND (keywords LIKE %s OR content LIKE %s)
                ORDER BY updated_at DESC
                LIMIT %s
            """
            args = (SOURCE_AGENT, f"%{query}%", f"%{query}%", limit)

        return self._query(sql, args)

    def write(self, content: str, category: str, keywords: str = "",
              is_index: int = 0, mysql_query: str = "", section: str = "memory",
              expires_at: Optional[datetime] = None) -> int:
        """Write a new memory entry.

        Returns the new entry id.
        """
        sql = """
            INSERT INTO memory_entries
                (source_agent, section, category, content, keywords,
                 is_index, mysql_query, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        args = (SOURCE_AGENT, section, category, content, keywords,
                is_index, mysql_query, expires_at)
        return self._execute(sql, args)

    def update(self, entry_id: int, content: str = None, keywords: str = None,
               is_index: int = None) -> bool:
        """Update an existing entry. Only updates non-None fields."""
        fields = []
        args = []
        if content is not None:
            fields.append("content = %s")
            args.append(content)
        if keywords is not None:
            fields.append("keywords = %s")
            args.append(keywords)
        if is_index is not None:
            fields.append("is_index = %s")
            args.append(is_index)
        if not fields:
            return False
        fields.append("updated_at = NOW()")
        args.append(entry_id)
        sql = f"UPDATE memory_entries SET {', '.join(fields)} WHERE id = %s AND source_agent = %s"
        args.append(SOURCE_AGENT)
        affected = self._execute(sql, tuple(args))
        return affected > 0

    def delete(self, entry_id: int) -> bool:
        """Delete an entry by id. Only deletes if source_agent matches."""
        sql = "DELETE FROM memory_entries WHERE id = %s AND source_agent = %s"
        affected = self._execute(sql, (entry_id, SOURCE_AGENT))
        return affected > 0

    def get_recent(self, *, limit: int = 20, section: str = "memory") -> List[Dict]:
        """Get most recently updated entries."""
        sql = """
            SELECT id, source_agent, section, category, content, keywords,
                   is_index, mysql_query, expires_at, created_at, updated_at
            FROM memory_entries
            WHERE source_agent = %s AND section = %s
            ORDER BY updated_at DESC
            LIMIT %s
        """
        return self._query(sql, (SOURCE_AGENT, section, limit))

    def get_by_category(self, category: str, *, limit: int = 50) -> List[Dict]:
        """Get all entries in a category."""
        sql = """
            SELECT id, source_agent, section, category, content, keywords,
                   is_index, mysql_query, expires_at, created_at, updated_at
            FROM memory_entries
            WHERE source_agent = %s AND section = 'memory' AND category = %s
            ORDER BY updated_at DESC
            LIMIT %s
        """
        return self._query(sql, (SOURCE_AGENT, category, limit))

    def get_indices(self) -> List[Dict]:
        """Get all index entries (is_index=1)."""
        sql = """
            SELECT id, source_agent, section, category, content, keywords,
                   is_index, mysql_query, expires_at, created_at, updated_at
            FROM memory_entries
            WHERE source_agent = %s AND is_index = 1
            ORDER BY category, id
        """
        return self._query(sql, (SOURCE_AGENT,))

    def count(self, section: str = "memory") -> int:
        """Return total entry count for this agent."""
        sql = "SELECT COUNT(*) as cnt FROM memory_entries WHERE source_agent = %s AND section = %s"
        rows = self._query(sql, (SOURCE_AGENT, section))
        return rows[0]["cnt"] if rows else 0

    # ── MemoryManager hooks ──────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Non-blocking turn sync — queue writes for background processing."""
        def _sync():
            try:
                # Don't log full content, just a summary
                preview = (user_content + assistant_content)[:200]
                logger.debug("[MySQLMemory] syncing turn: %s...", preview)
            except Exception as e:
                logger.warning("[MySQLMemory] sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)
        self._sync_thread = threading.Thread(target=_sync, daemon=True)
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Sync built-in memory writes to MySQL by reading the staging file.

        staging file (memory_staging.md) is written by MemoryStore on every mutation.
        We detect changes via hash and sync only when the file has changed.
        """
        if target != "memory":
            return

        import hashlib
        from pathlib import Path
        from hermes_constants import get_hermes_home

        staging_path = get_hermes_home() / "memories" / "memory_staging.md"
        if not staging_path.exists():
            return

        # Check if staging file changed
        file_hash = hashlib.md5(staging_path.read_bytes()).hexdigest()
        if file_hash == self._last_staging_hash:
            return  # No change since last sync
        self._last_staging_hash = file_hash

        # Read and parse staging file — split by § entry delimiter
        raw = staging_path.read_text(encoding="utf-8")
        if not raw.strip():
            return

        ENTRY_DELIMITER = "\n§\n"
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]

        for entry in entries:
            # Skip protected block marker if somehow present
            if entry.startswith("[PROTECTED_BLOCK]"):
                continue
            self.write(content=entry, category="memory_backup",
                      keywords="auto_sync,staging", section="memory")

    def on_session_end(self, messages: List[Dict]) -> None:
        """Flush any pending writes at session end."""
        logger.info("[MySQLMemory] Session end, entries stored: %d", self.count())

    def shutdown(self) -> None:
        if self._conn:
            try:
                self._conn.close()
                logger.info("[MySQLMemory] Connection closed")
            except Exception as e:
                logger.warning("[MySQLMemory] Close error: %s", e)

    # ── Tool schemas (exposed to the model) ─────────────────────────────────

    def get_tool_schemas(self) -> List[Dict]:
        return [
            {
                "name": "mysql_memory_search",
                "description": "Search Hermes's long-term memory in MySQL. "
                               "Use when you need to recall facts, preferences, "
                               "or context from previous sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text search query (matches keywords + content)"
                        },
                        "category": {
                            "type": "string",
                            "description": "Optional category filter (e.g. 'mysql', '持仓', '脚本')"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 10, max 50)",
                            "default": 10
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "mysql_memory_write",
                "description": "Write a new entry to Hermes's MySQL-backed memory. "
                               "Use for important facts, preferences, or context "
                               "that should persist across sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The full memory content to store"
                        },
                        "category": {
                            "type": "string",
                            "description": "Category/classification (e.g. 'mysql', '持仓', '协作规则', '环境')"
                        },
                        "keywords": {
                            "type": "string",
                            "description": "Comma-separated quick-search keywords"
                        },
                        "is_index": {
                            "type": "integer",
                            "description": "Set to 1 to mark as an index entry (for the MEMORY.md index)",
                            "default": 0
                        },
                        "mysql_query": {
                            "type": "string",
                            "description": "The MySQL query string for index entries"
                        }
                    },
                    "required": ["content", "category"]
                }
            },
            {
                "name": "mysql_memory_get_recent",
                "description": "Get the most recently updated memory entries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of entries to return (default 20)",
                            "default": 20
                        }
                    }
                }
            },
            {
                "name": "mysql_memory_get_indices",
                "description": "Get all index entries (is_index=1) — the memory catalog.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            },
        ]

    def handle_tool_call(self, name: str, args: Dict) -> str:
        handlers = {
            "mysql_memory_search": lambda a: json.dumps(self.search(
                query=a.get("query", ""),
                category=a.get("category", ""),
                limit=min(a.get("limit", 10), 50),
            ), ensure_ascii=False, default=str),
            "mysql_memory_write": lambda a: json.dumps({
                "id": self.write(
                    content=a["content"],
                    category=a["category"],
                    keywords=a.get("keywords", ""),
                    is_index=a.get("is_index", 0),
                    mysql_query=a.get("mysql_query", ""),
                )
            }, ensure_ascii=False),
            "mysql_memory_get_recent": lambda a: json.dumps(
                self.get_recent(limit=min(a.get("limit", 20), 100)),
                ensure_ascii=False, default=str
            ),
            "mysql_memory_get_indices": lambda a: json.dumps(
                self.get_indices(),
                ensure_ascii=False, default=str
            ),
        }
        handler = handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            return handler(args)
        except Exception as e:
            logger.exception("[MySQLMemory] tool=%s error: %s", name, e)
            return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_memory_provider(MySQLMemoryProvider())
