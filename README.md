# Raindrop → Notion Incremental Sync

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/packaging-uv-de5fe9.svg)](https://github.com/astral-sh/uv)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

One-way **incremental** sync of [Raindrop.io](https://raindrop.io) bookmarks (raindrops) into a [Notion](https://notion.so) data source table.

Built from first principles against the official APIs:

- Raindrop: https://developer.raindrop.io  
- Notion (data sources / pages): https://developers.notion.com  

## Why this exists

Raindrop is excellent for capturing and organising links.  
Notion is excellent for structured knowledge, views, relations and collaboration.  

This tool keeps a Notion table in sync with your Raindrop library so you can query, filter and relate bookmarks inside Notion without manual export/import.

## Features

- **True incremental sync** – walks raindrops newest-first (`sort=-lastUpdate`) and stops as soon as it reaches items older than the last successful watermark.
- **Content-hash short-circuit** – unchanged items never touch the Notion API.
- **Idempotent** – safe to re-run; state lives in a local SQLite file.
- **Periodic full reconciliation** – every N runs (or with `--full`) also detects deletions and archives the corresponding Notion pages.
- **Rate-limit aware** – respects Raindrop (120 req/min) and Notion (~3 req/s) limits with retries and `Retry-After`.
- **Modern Python** – type hints, `uv`, `httpx`, `tenacity`, clean package structure.

## Architecture (ASCII)

```
┌────────────────────┐     extract      ┌──────────────────┐     transform     ┌────────────────────┐
│  Raindrop.io API   │ ───────────────► │  Sync Engine     │ ───────────────► │  Notion Data Source│
│  (collections +    │   (paginate,     │  (Python + uv)   │   (map fields,   │  (pages = rows)    │
│   raindrops)       │    rate-limit)   │                  │    upsert)       │                    │
└─────────┬──────────┘                  └────────┬─────────┘                  └─────────┬──────────┘
          │                                      │                                       │
          │ OAuth / Test Token                   │ SQLite state                          │ Integration
          │ Authorization: Bearer                │ (raindrop_id ↔ notion_page_id,        │ Token +
          │                                      │  last_sync, content_hash)             │ Notion-Version
          ▼                                      ▼                                       ▼
     Rate: 120/min                          Idempotent                            Rate: ~3/s avg
```

## Incremental algorithm

Raindrop’s public API has **no server-side “updated after” filter**.  
The algorithm therefore:

1. Requests raindrops sorted by `-lastUpdate` (newest first).
2. Walks pages until it hits items whose `lastUpdate ≤ last_sync_ts`.
3. For each newer item computes a stable content hash.
4. If the hash matches the stored value → only updates `last_seen` (no Notion write).
5. Otherwise creates or updates the Notion page and stores the new hash + page ID.
6. Advances the watermark (`last_sync_ts`) only after a successful run.
7. Every N runs (default 24) or with `--full` performs a full pass that also archives pages for raindrops that disappeared.

## Prerequisites

1. **Raindrop token**  
   - Easiest: Test Token from https://app.raindrop.io/settings/integrations (does not expire).  
   - Or a proper OAuth access token.

2. **Notion integration**  
   - Create an internal integration at https://www.notion.so/my-integrations.  
   - Share the target database with the integration.  
   - Copy the integration secret.

3. **Notion data source**  
   - Create a database (or use an existing one).  
   - Note its **data source ID** (since Notion API 2025-09-03 databases can contain multiple data sources).  
   - Create (or let the first run guide you) these properties (names must match or be adjusted in `mapper.py`):

| Property        | Type          | Notes                          |
|-----------------|---------------|--------------------------------|
| Title           | title         | Required                       |
| URL             | url           |                                |
| Raindrop ID     | number        | Unique key                     |
| Excerpt         | rich_text     |                                |
| Note            | rich_text     |                                |
| Tags            | multi_select  |                                |
| Type            | select        | link / article / image / …     |
| Important       | checkbox      |                                |
| Broken          | checkbox      |                                |
| Domain          | rich_text     |                                |
| Collection ID   | number        |                                |
| Created         | date          |                                |
| Last Update     | date          |                                |
| Cover           | files         | external URL                   |

## Quick start

```bash
# Clone
git clone https://github.com/raphaelmansuy/raindrop-notion-sync.git
cd raindrop-notion-sync

# Install with uv (recommended)
uv sync

# Configure
cp .env.example .env
# edit .env → set RAINDROP_TOKEN, NOTION_TOKEN, NOTION_DATA_SOURCE_ID

# First run (incremental with 30-day lookback on cold start)
uv run raindrop-notion-sync

# Force full reconciliation + delete detection
uv run raindrop-notion-sync --full

# Verbose
uv run raindrop-notion-sync -v
```

State is stored in `sync_state.db` (SQLite) next to the working directory.  
You can change the path with `STATE_DB_PATH`.

## Configuration (environment)

| Variable                  | Required | Default          | Description                              |
|---------------------------|----------|------------------|------------------------------------------|
| `RAINDROP_TOKEN`          | yes      | –                | Test token or OAuth access token         |
| `NOTION_TOKEN`            | yes      | –                | Notion integration secret                |
| `NOTION_DATA_SOURCE_ID`   | yes      | –                | Target data source UUID                  |
| `RAINDROP_COLLECTION_ID`  | no       | `0`              | `0` = all, or a specific collection ID   |
| `STATE_DB_PATH`           | no       | `sync_state.db`  | SQLite file location                     |

## Project layout

```
src/raindrop_notion_sync/
├── __init__.py      # entry point
├── cli.py           # argparse CLI
├── config.py        # env / dataclass config
├── raindrop.py      # Raindrop HTTP client + pagination
├── notion.py        # Notion HTTP client (data_source aware)
├── mapper.py        # raindrop → Notion properties + content hash
├── state.py         # SQLite mapping + watermark
└── sync.py          # incremental + full orchestration
```

## Rate limits & reliability

- **Raindrop**: 120 requests per minute → client enforces ~0.5 s spacing + exponential backoff.
- **Notion**: ~3 requests per second average → client enforces ~0.35 s spacing and respects the `Retry-After` header on 429 / 529.
- All network calls use `tenacity` for retries on transient errors.

## License

MIT

## Acknowledgements

- Official Raindrop.io API documentation  
- Official Notion API documentation (especially the 2025-09-03 data-source model)
