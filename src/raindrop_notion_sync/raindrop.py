"""Raindrop.io API client – focused on listing raindrops for incremental sync."""

from __future__ import annotations

import time
from typing import Any, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import Config


class RateLimitError(Exception):
    pass


class RaindropClient:
    BASE = "https://api.raindrop.io/rest/v1"

    def __init__(self, config: Config):
        self.config = config
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {config.raindrop_token}"},
            timeout=config.request_timeout,
        )
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RaindropClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.config.raindrop_min_interval_s:
            time.sleep(self.config.raindrop_min_interval_s - elapsed)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, RateLimitError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        self._throttle()
        r = self._client.get(f"{self.BASE}{path}", params=params or {})
        if r.status_code == 429:
            # Raindrop rate limit headers exist; simple backoff
            raise RateLimitError("Raindrop rate limited")
        r.raise_for_status()
        data = r.json()
        if not data.get("result", True):
            raise RuntimeError(f"Raindrop API error: {data}")
        return data

    def iter_raindrops(
        self,
        collection_id: Optional[int] = None,
        *,
        sort: str = "-lastUpdate",
        perpage: int = 50,
        nested: bool = True,
        max_pages: Optional[int] = None,
    ) -> Generator[dict, None, None]:
        """
        Yield raindrop objects newest-first (when sort=-lastUpdate).

        Stops when an empty page is returned.
        Caller is responsible for early-exit based on lastUpdate for incremental.
        """
        cid = collection_id if collection_id is not None else self.config.raindrop_collection_id
        page = 0
        while True:
            if max_pages is not None and page >= max_pages:
                break
            data = self._get(
                f"/raindrops/{cid}",
                params={
                    "page": page,
                    "perpage": perpage,
                    "sort": sort,
                    "nested": str(nested).lower(),
                },
            )
            items = data.get("items") or []
            if not items:
                break
            for item in items:
                yield item
            page += 1

    def get_raindrop(self, raindrop_id: int) -> dict:
        data = self._get(f"/raindrop/{raindrop_id}")
        return data.get("item") or data
