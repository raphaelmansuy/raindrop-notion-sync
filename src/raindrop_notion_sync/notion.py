"""Notion API client – create / update pages under a data source."""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import Config


class NotionRateLimitError(Exception):
    def __init__(self, retry_after: float = 1.0):
        self.retry_after = retry_after
        super().__init__(f"Notion rate limited, retry after {retry_after}s")


class NotionClient:
    BASE = "https://api.notion.com/v1"
    VERSION = "2026-03-11"

    def __init__(self, config: Config):
        self.config = config
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {config.notion_token}",
                "Notion-Version": self.VERSION,
                "Content-Type": "application/json",
            },
            timeout=config.request_timeout,
        )
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NotionClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.config.notion_min_interval_s:
            time.sleep(self.config.notion_min_interval_s - elapsed)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, NotionRateLimitError)),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        reraise=True,
    )
    def _request(self, method: str, path: str, json: Optional[dict] = None) -> dict:
        self._throttle()
        r = self._client.request(method, f"{self.BASE}{path}", json=json)
        if r.status_code in (429, 529):
            retry_after = float(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            raise NotionRateLimitError(retry_after)
        if r.status_code >= 400:
            # surface body for debugging
            try:
                body = r.json()
            except Exception:
                body = r.text
            raise httpx.HTTPStatusError(
                f"{r.status_code} {body}", request=r.request, response=r
            )
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()

    def create_page(self, properties: dict) -> dict:
        """Create a page under the configured data source."""
        body = {
            "parent": {"data_source_id": self.config.notion_data_source_id},
            "properties": properties,
        }
        return self._request("POST", "/pages", json=body)

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self._request("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def archive_page(self, page_id: str) -> dict:
        """Soft-delete by archiving (in_trash / archived)."""
        # Notion uses "archived": true on the page object
        return self._request("PATCH", f"/pages/{page_id}", json={"archived": True})

    def retrieve_data_source(self) -> dict:
        return self._request("GET", f"/data_sources/{self.config.notion_data_source_id}")
