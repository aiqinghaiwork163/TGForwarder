"""SQLite 持久化层，替代 JSON 文件存储。"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


class Database:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS forwarder_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS channel_state (
        channel_name TEXT PRIMARY KEY,
        last_message_id INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS forwarded_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        link_key TEXT NOT NULL,
        url TEXT,
        target_channel TEXT,
        created_at TEXT,
        UNIQUE(link_key, target_channel)
    );
    CREATE TABLE IF NOT EXISTS link_check_messages (
        message_id INTEGER PRIMARY KEY,
        urls TEXT NOT NULL,
        invalid_urls TEXT NOT NULL DEFAULT '[]',
        is_123 INTEGER NOT NULL DEFAULT 0,
        edited INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS link_check_meta (
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        target_channel TEXT,
        title TEXT,
        year TEXT,
        rating REAL,
        genres TEXT,
        link_keys TEXT,
        raw_text TEXT,
        tmdb_id INTEGER,
        source_channel TEXT,
        created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_resources_title ON resources(title);
    CREATE INDEX IF NOT EXISTS idx_resources_created ON resources(created_at);
    CREATE INDEX IF NOT EXISTS idx_forwarded_link_key ON forwarded_links(link_key);
    """

    def __init__(self, db_path: str = "data/tgforwarder.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)

    # ── Forwarder state ──────────────────────────────────────────

    def load_forwarder_state(self) -> dict:
        """加载转发器状态（兼容原 history.json 结构）。"""
        state = {
            "links": [], "link_keys": [], "sizes": [], "bot_links": {},
            "chat_forward_count_msg_id": {}, "channel_state": {},
            "today": "", "today_count": 0,
        }
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM forwarder_meta").fetchall()
            for row in rows:
                key, val = row["key"], row["value"]
                if key in ("links", "link_keys", "sizes"):
                    state[key] = json.loads(val)
                elif key in ("bot_links", "chat_forward_count_msg_id", "channel_state"):
                    state[key] = json.loads(val)
                elif key in ("today",):
                    state[key] = val
                elif key == "today_count":
                    state[key] = int(val)
            cs = conn.execute("SELECT channel_name, last_message_id FROM channel_state").fetchall()
            for row in cs:
                state["channel_state"][row["channel_name"]] = row["last_message_id"]
        return state

    def save_forwarder_state(self, state: dict):
        with self._conn() as conn:
            meta_map = {
                "links": json.dumps(state.get("links", []), ensure_ascii=False),
                "link_keys": json.dumps(state.get("link_keys", []), ensure_ascii=False),
                "sizes": json.dumps(state.get("sizes", []), ensure_ascii=False),
                "bot_links": json.dumps(state.get("bot_links", {}), ensure_ascii=False),
                "chat_forward_count_msg_id": json.dumps(
                    state.get("chat_forward_count_msg_id", {}), ensure_ascii=False
                ),
                "today": state.get("today", ""),
                "today_count": str(state.get("today_count", 0)),
            }
            for key, val in meta_map.items():
                conn.execute(
                    "INSERT OR REPLACE INTO forwarder_meta (key, value) VALUES (?, ?)",
                    (key, val),
                )
            for channel, mid in state.get("channel_state", {}).items():
                conn.execute(
                    """INSERT OR REPLACE INTO channel_state
                       (channel_name, last_message_id, updated_at) VALUES (?, ?, ?)""",
                    (channel, mid, datetime.now().isoformat()),
                )

    def migrate_from_json(self, history_path: str = "history.json"):
        if not os.path.exists(history_path):
            return False
        with open(history_path, encoding="utf-8") as f:
            state = json.load(f)
        self.save_forwarder_state(state)
        backup = history_path + ".bak"
        os.rename(history_path, backup)
        return True

    # ── Link checker ───────────────────────────────────────────

    def get_link_check_last_id(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM link_check_meta WHERE key='last_processed_id'"
            ).fetchone()
            return row["value"] if row else 0

    def set_link_check_last_id(self, mid: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO link_check_meta (key, value) VALUES ('last_processed_id', ?)",
                (mid,),
            )

    def upsert_link_check_message(self, message_id: int, urls: list, is_123: bool = False):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO link_check_messages
                   (message_id, urls, invalid_urls, is_123, edited)
                   VALUES (?, ?, '[]', ?, 0)""",
                (message_id, json.dumps(urls, ensure_ascii=False), int(is_123)),
            )

    def get_all_link_check_messages(self, is_123: bool | None = None) -> list[dict]:
        with self._conn() as conn:
            if is_123 is True:
                rows = conn.execute(
                    "SELECT * FROM link_check_messages WHERE is_123=1"
                ).fetchall()
            elif is_123 is False:
                rows = conn.execute(
                    "SELECT * FROM link_check_messages WHERE is_123=0"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM link_check_messages").fetchall()
            return [self._row_to_link_msg(r) for r in rows]

    def _row_to_link_msg(self, row) -> dict:
        return {
            "message_id": row["message_id"],
            "urls": json.loads(row["urls"]),
            "invalid_urls": json.loads(row["invalid_urls"]),
            "is_123": bool(row["is_123"]),
            "edited": bool(row["edited"]),
        }

    def update_link_check_invalid(self, message_id: int, invalid_urls: list):
        with self._conn() as conn:
            conn.execute(
                "UPDATE link_check_messages SET invalid_urls=? WHERE message_id=?",
                (json.dumps(invalid_urls, ensure_ascii=False), message_id),
            )

    def mark_link_check_edited(self, message_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE link_check_messages SET edited=1 WHERE message_id=?", (message_id,)
            )

    def remove_link_check_message(self, message_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM link_check_messages WHERE message_id=?", (message_id,))

    def migrate_link_check_json(self, normal_path: str, path_123: str):
        migrated = False
        last_id = 0
        for path, is_123 in [(normal_path, False), (path_123, True)]:
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            last_id = max(last_id, data.get("last_processed_id", 0))
            for msg in data.get("messages", []):
                self.upsert_link_check_message(msg["message_id"], msg["urls"], is_123)
                if msg.get("invalid_urls"):
                    self.update_link_check_invalid(msg["message_id"], msg["invalid_urls"])
            os.rename(path, path + ".bak")
            migrated = True
        if migrated and last_id:
            self.set_link_check_last_id(last_id)
        return migrated

    # ── Resources (search index) ───────────────────────────────

    def index_resource(
        self,
        message_id: int | None,
        target_channel: str,
        title: str,
        raw_text: str,
        link_keys: list,
        source_channel: str = "",
        year: str = "",
        rating: float | None = None,
        genres: str = "",
        tmdb_id: int | None = None,
    ):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resources
                   (message_id, target_channel, title, year, rating, genres,
                    link_keys, raw_text, tmdb_id, source_channel, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id, target_channel, title, year, rating, genres,
                    json.dumps(link_keys, ensure_ascii=False), raw_text,
                    tmdb_id, source_channel, datetime.now().isoformat(),
                ),
            )

    def search_resources(self, query: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM resources
                   WHERE title LIKE ? OR raw_text LIKE ?
                   ORDER BY created_at DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as conn:
            total_resources = conn.execute("SELECT COUNT(*) AS c FROM resources").fetchone()["c"]
            today_resources = conn.execute(
                "SELECT COUNT(*) AS c FROM resources WHERE created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()["c"]
            total_links = conn.execute("SELECT COUNT(*) AS c FROM link_check_messages").fetchone()["c"]
            invalid_links = conn.execute(
                "SELECT COUNT(*) AS c FROM link_check_messages WHERE invalid_urls != '[]'"
            ).fetchone()["c"]
            channels = conn.execute("SELECT COUNT(*) AS c FROM channel_state").fetchone()["c"]
        return {
            "total_resources": total_resources,
            "today_resources": today_resources,
            "total_link_messages": total_links,
            "invalid_link_messages": invalid_links,
            "monitored_channels": channels,
        }

    # ── Bot settings ───────────────────────────────────────────

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM forwarder_meta WHERE key=?", (f"bot_{key}",)
            ).fetchone()
            if row is None:
                return default
            val = row["value"]
            if val.lower() == "true":
                return True
            if val.lower() == "false":
                return False
            return val

    def set_setting(self, key: str, value: Any):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO forwarder_meta (key, value) VALUES (?, ?)",
                (f"bot_{key}", str(value)),
            )

    def is_paused(self) -> bool:
        return bool(self.get_setting("paused", False))

    def set_paused(self, paused: bool):
        self.set_setting("paused", paused)
