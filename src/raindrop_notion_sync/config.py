"""Configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    raindrop_token: str
    notion_token: str
    notion_data_source_id: str
    raindrop_collection_id: int = 0  # 0 = all raindrops
    state_db_path: Path = Path("sync_state.db")
    # Incremental: how far back to look on first run or after long gaps (hours)
    lookback_hours_on_cold_start: int = 24 * 30  # 30 days
    # After this many incremental runs, force a full reconciliation pass
    full_reconcile_every_n_runs: int = 24
    request_timeout: float = 30.0
    notion_min_interval_s: float = 0.35  # ~3 rps average
    raindrop_min_interval_s: float = 0.5  # stay well under 120/min


def load_config() -> Config:
    token = os.getenv("RAINDROP_TOKEN")
    notion = os.getenv("NOTION_TOKEN")
    ds = os.getenv("NOTION_DATA_SOURCE_ID")
    if not token or not notion or not ds:
        raise SystemExit(
            "Missing required env vars: RAINDROP_TOKEN, NOTION_TOKEN, NOTION_DATA_SOURCE_ID"
        )
    coll = int(os.getenv("RAINDROP_COLLECTION_ID", "0"))
    db = Path(os.getenv("STATE_DB_PATH", "sync_state.db"))
    return Config(
        raindrop_token=token,
        notion_token=notion,
        notion_data_source_id=ds,
        raindrop_collection_id=coll,
        state_db_path=db,
    )
