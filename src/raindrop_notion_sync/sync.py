"""
Incremental + full sync orchestration.

Incremental strategy (first principles, given Raindrop API constraints):
- Raindrop has no server-side "updated_after" filter.
- We request raindrops sorted by -lastUpdate (newest first).
- Walk pages until we encounter items whose lastUpdate <= last_sync_ts.
- For each candidate newer than last_sync_ts (or all on cold start):
    - compute content hash
    - if hash matches stored → only touch last_seen
    - else create or update Notion page and store new hash + page_id
- Advance last_sync_ts only after a successful run to the highest lastUpdate observed
  (or current time if none).
- Periodically (every N runs) perform a full reconciliation to catch deletes
  and any items that may have been missed due to clock skew / race.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import Config
from .mapper import content_hash, to_notion_properties
from .notion import NotionClient
from .raindrop import RaindropClient
from .state import SyncState, _utc_now_iso

log = logging.getLogger(__name__)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    # Accept both ...Z and +00:00
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _ts_gt(a: Optional[str], b: Optional[str]) -> bool:
    """Return True if a > b (ISO timestamps). Missing values treated as very old."""
    da, db = _parse_ts(a), _parse_ts(b)
    if da is None:
        return False
    if db is None:
        return True
    return da > db


class SyncEngine:
    def __init__(self, config: Config):
        self.config = config
        self.state = SyncState(config.state_db_path)

    def run(self, *, force_full: bool = False) -> dict:
        """
        Execute one sync cycle.
        Returns stats dict.
        """
        run_n = self.state.increment_run_count()
        do_full = force_full or (run_n % self.config.full_reconcile_every_n_runs == 0)

        log.info(
            "Starting sync run #%s  mode=%s  collection=%s",
            run_n,
            "FULL" if do_full else "INCREMENTAL",
            self.config.raindrop_collection_id,
        )

        stats = {
            "run": run_n,
            "mode": "full" if do_full else "incremental",
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "archived": 0,
            "errors": 0,
            "stopped_early": False,
        }

        with RaindropClient(self.config) as rd, NotionClient(self.config) as nt:
            if do_full:
                self._full_reconcile(rd, nt, stats)
            else:
                self._incremental(rd, nt, stats)

        log.info("Sync finished: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Incremental path
    # ------------------------------------------------------------------
    def _incremental(
        self,
        rd: RaindropClient,
        nt: NotionClient,
        stats: dict,
    ) -> None:
        last_sync = self.state.get_last_sync_ts()
        if last_sync is None:
            # Cold start: limit lookback so we don't pull the entire library on first run
            # unless the user forces full.
            cold = datetime.now(timezone.utc) - timedelta(
                hours=self.config.lookback_hours_on_cold_start
            )
            last_sync = cold.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            log.info("Cold start – using lookback last_sync=%s", last_sync)

        max_seen_update: Optional[str] = None
        seen_ids: set[int] = set()

        for item in rd.iter_raindrops(sort="-lastUpdate"):
            stats["fetched"] += 1
            rid = item.get("_id")
            if rid is None:
                continue
            seen_ids.add(rid)

            item_updated = item.get("lastUpdate") or item.get("created")
            if not _ts_gt(item_updated, last_sync):
                # Because we sorted newest-first, the rest of this page and later
                # pages are older → safe to stop.
                stats["stopped_early"] = True
                log.info(
                    "Reached items older than last_sync (%s) – stopping pagination",
                    last_sync,
                )
                break

            if max_seen_update is None or _ts_gt(item_updated, max_seen_update):
                max_seen_update = item_updated

            try:
                self._upsert_one(rd, nt, item, stats)
            except Exception as e:
                stats["errors"] += 1
                log.exception("Failed to upsert raindrop %s: %s", rid, e)

        # Advance watermark only if we made progress without catastrophic failure
        if stats["errors"] == 0 or (stats["created"] + stats["updated"] > 0):
            # Prefer the newest lastUpdate we actually processed; fall back to now
            new_ts = max_seen_update or _utc_now_iso()
            # Never move watermark backwards
            if last_sync is None or _ts_gt(new_ts, last_sync):
                self.state.set_last_sync_ts(new_ts)
                log.info("Advanced last_sync_ts → %s", new_ts)

    # ------------------------------------------------------------------
    # Full reconciliation (catches deletes + any drift)
    # ------------------------------------------------------------------
    def _full_reconcile(
        self,
        rd: RaindropClient,
        nt: NotionClient,
        stats: dict,
    ) -> None:
        log.info("Running full reconciliation pass")
        current_ids: set[int] = set()
        max_seen_update: Optional[str] = None

        for item in rd.iter_raindrops(sort="-lastUpdate"):
            stats["fetched"] += 1
            rid = item.get("_id")
            if rid is None:
                continue
            current_ids.add(rid)

            item_updated = item.get("lastUpdate") or item.get("created")
            if max_seen_update is None or _ts_gt(item_updated, max_seen_update):
                max_seen_update = item_updated

            try:
                self._upsert_one(rd, nt, item, stats)
            except Exception as e:
                stats["errors"] += 1
                log.exception("Failed to upsert raindrop %s: %s", rid, e)

        # Detect deletions: items we previously tracked as active but are gone
        known_active = self.state.all_active_raindrop_ids()
        deleted = known_active - current_ids
        for rid in deleted:
            page_id = self.state.get_page_id(rid)
            if page_id:
                try:
                    nt.archive_page(page_id)
                    self.state.mark_inactive(rid)
                    stats["archived"] += 1
                    log.info("Archived Notion page for deleted raindrop %s", rid)
                except Exception as e:
                    stats["errors"] += 1
                    log.exception("Failed to archive page for %s: %s", rid, e)

        if max_seen_update:
            self.state.set_last_sync_ts(max_seen_update)
            log.info("Full reconcile advanced last_sync_ts → %s", max_seen_update)

    # ------------------------------------------------------------------
    # Single-item upsert with hash short-circuit
    # ------------------------------------------------------------------
    def _upsert_one(
        self,
        rd: RaindropClient,
        nt: NotionClient,
        item: dict,
        stats: dict,
    ) -> None:
        rid = item["_id"]
        h = content_hash(item)
        existing_hash = self.state.get_hash(rid)
        page_id = self.state.get_page_id(rid)

        if existing_hash == h and page_id:
            # No content change – just refresh last_seen
            self.state.touch_last_seen(rid)
            stats["unchanged"] += 1
            return

        props = to_notion_properties(item)

        if page_id:
            nt.update_page(page_id, props)
            self.state.upsert_mapping(rid, page_id, h)
            stats["updated"] += 1
            log.debug("Updated raindrop %s → page %s", rid, page_id)
        else:
            resp = nt.create_page(props)
            new_id = resp["id"]
            self.state.upsert_mapping(rid, new_id, h)
            stats["created"] += 1
            log.debug("Created raindrop %s → page %s", rid, new_id)
