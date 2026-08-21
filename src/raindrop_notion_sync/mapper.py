"""Map Raindrop raindrop objects → Notion page properties + content hash."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _text(content: str | None, limit: int = 1900) -> list[dict]:
    if not content:
        return []
    content = content[:limit]
    return [{"type": "text", "text": {"content": content}}]


def content_hash(rd: dict) -> str:
    """Stable hash of fields we care about for change detection."""
    relevant = {
        "title": rd.get("title"),
        "link": rd.get("link"),
        "excerpt": rd.get("excerpt"),
        "note": rd.get("note"),
        "tags": sorted(rd.get("tags") or []),
        "type": rd.get("type"),
        "important": bool(rd.get("important")),
        "cover": rd.get("cover"),
        "broken": bool(rd.get("broken")),
        "collection": (rd.get("collection") or {}).get("$id")
        or (rd.get("collection") or {}).get("id"),
        "lastUpdate": rd.get("lastUpdate"),
    }
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def to_notion_properties(rd: dict) -> dict[str, Any]:
    """
    Convert a Raindrop item to Notion properties.
    Property *names* must match the data source schema exactly.
    Adjust names here if your Notion columns differ.
    """
    title = (rd.get("title") or rd.get("link") or "Untitled")[:2000]
    tags = [{"name": str(t)[:100]} for t in (rd.get("tags") or []) if t]
    coll = rd.get("collection") or {}
    coll_id = coll.get("$id") or coll.get("id")

    props: dict[str, Any] = {
        "Title": {"title": _text(title)},
        "URL": {"url": rd.get("link")},
        "Raindrop ID": {"number": rd.get("_id")},
        "Excerpt": {"rich_text": _text(rd.get("excerpt"))},
        "Note": {"rich_text": _text(rd.get("note"))},
        "Tags": {"multi_select": tags},
        "Important": {"checkbox": bool(rd.get("important"))},
        "Broken": {"checkbox": bool(rd.get("broken"))},
        "Domain": {"rich_text": _text(rd.get("domain"))},
    }

    if rd.get("type"):
        props["Type"] = {"select": {"name": str(rd["type"])[:100]}}

    if coll_id is not None:
        props["Collection ID"] = {"number": coll_id}

    # Dates – Notion date property accepts start (date or datetime)
    if rd.get("created"):
        props["Created"] = {"date": {"start": rd["created"]}}
    if rd.get("lastUpdate"):
        props["Last Update"] = {"date": {"start": rd["lastUpdate"]}}

    # Cover as external file if present
    cover = rd.get("cover")
    if cover and isinstance(cover, str) and cover.startswith("http"):
        props["Cover"] = {
            "files": [
                {
                    "name": "cover",
                    "external": {"url": cover},
                }
            ]
        }

    return props
