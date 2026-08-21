"""Durable state for incremental sync: ID map, hashes, cursors, run counters."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SyncState:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._tx() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS raindrop_map (
                    raindrop_id   INTEGER PRIMARY KEY,
                    notion_page_id TEXT NOT NULL,
                    content_hash  TEXT,
                    last_seen     TEXT NOT NULL,  -- ISO when we last saw it in source
                    last_synced   TEXT NOT NULL,  -- ISO when we last wrote to Notion
                    is_active     INTEGER NOT NULL DEFAULT 1  -- 0 = archived / deleted from source
                );

                CREATE INDEX IF NOT EXISTS idx_map_active ON raindrop_map(is_active);
                CREATE INDEX IF NOT EXISTS idx_map_last_seen ON raindrop_map(last_seen);
                """
            )

    # ---- meta helpers ----
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._tx() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_last_sync_ts(self) -> Optional[str]:
        return self.get_meta("last_sync_ts")

    def set_last_sync_ts(self, ts: str) -> None:
        self.set_meta("last_sync_ts", ts)

    def get_run_count(self) -> int:
        v = self.get_meta("run_count", "0")
        return int(v or 0)

    def increment_run_count(self) -> int:
        n = self.get_run_count() + 1
        self.set_meta("run_count", str(n))
        return n

    # ---- mapping helpers ----
    def get_page_id(self, raindrop_id: int) -> Optional[str]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT notion_page_id FROM raindrop_map WHERE raindrop_id = ?",
                (raindrop_id,),
            ).fetchone()
            return row["notion_page_id"] if row else None

    def get_hash(self, raindrop_id: int) -> Optional[str]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT content_hash FROM raindrop_map WHERE raindrop_id = ?",
                (raindrop_id,),
            ).fetchone()
            return row["content_hash"] if row else None

    def upsert_mapping(
        self,
        raindrop_id: int,
        notion_page_id: str,
        content_hash: str,
        *,
        is_active: bool = True,
    ) -> None:
        now = _utc_now_iso()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO raindrop_map
                    (raindrop_id, notion_page_id, content_hash, last_seen, last_synced, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(raindrop_id) DO UPDATE SET
                    notion_page_id = excluded.notion_page_id,
                    content_hash   = excluded.content_hash,
                    last_seen      = excluded.last_seen,
                    last_synced    = excluded.last_synced,
                    is_active      = excluded.is_active
                """,
                (raindrop_id, notion_page_id, content_hash, now, now, 1 if is_active else 0),
            )

    def touch_last_seen(self, raindrop_id: int) -> None:
        now = _utc_now_iso()
        with self._tx() as conn:
            conn.execute(
                "UPDATE raindrop_map SET last_seen = ?, is_active = 1 WHERE raindrop_id = ?",
                (now, raindrop_id),
            )

    def mark_inactive(self, raindrop_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE raindrop_map SET is_active = 0 WHERE raindrop_id = ?",
                (raindrop_id,),
            )

    def all_active_raindrop_ids(self) -> set[int]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT raindrop_id FROM raindrop_map WHERE is_active = 1"
            ).fetchall()
            return {r["raindrop_id"] for r in rows}

    def get_page_id_for_inactive(self) -> list[tuple[int, str]]:
        """Return (raindrop_id, notion_page_id) for items marked inactive."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT raindrop_id, notion_page_id FROM raindrop_map WHERE is_active = 0"
            ).fetchall()
            return [(r["raindrop_id"], r["notion_page_id"]) for r in rows]
