#!/usr/bin/env python3
"""
Smithery Registry Puller

Fetches all MCP servers from the Smithery registry at https://registry.smithery.ai
Smithery provides FULL tool definitions directly in their API, no introspection needed!

Features:
- Incremental updates using page-based pagination
- Tracks last sync for future runs
- Stores individual server files and combined index
- Extracts complete tool definitions with input schemas

Usage:
    python pull_smithery.py [--full] [--limit N]

Environment:
    SMITHERY_BEARER_AUTH - API key for Smithery (required)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# Configuration
SMITHERY_API_URL = "https://registry.smithery.ai"
DEFAULT_PAGE_SIZE = 50  # Smithery uses page-based pagination
REQUEST_TIMEOUT = 60  # Smithery can be slow with full tool data
MAX_RETRIES = 3
RETRY_DELAY = 2

# API Key - loaded from environment only (no hardcoded secrets)
# Set SMITHERY_API_KEY in .env file or environment

# Paths
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "sources" / "smithery"
SERVERS_DIR = DATA_DIR / "servers"
STATE_FILE = DATA_DIR / "sync_state.json"
INDEX_FILE = DATA_DIR / "index.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class SyncState:
    """Manages synchronization state."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load state: {e}")
        return {
            "last_sync": None,
            "total_servers": 0,
            "last_page": 0,
            "sync_history": []
        }

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)


class SmitheryClient:
    """Client for Smithery Registry API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "ERC8004-MCP-Sync/1.0"
        })

    def _request_with_retry(self, url: str, params: dict = None) -> dict:
        """Make request with retry logic."""
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                if response.status_code == 429:
                    wait_time = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    last_error = e
                elif response.status_code >= 500:
                    wait_time = RETRY_DELAY * (attempt + 1)
                    logger.warning(f"Server error {response.status_code}. Retrying...")
                    time.sleep(wait_time)
                    last_error = e
                else:
                    raise
            except requests.exceptions.RequestException as e:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"Request failed: {e}. Retrying...")
                time.sleep(wait_time)
                last_error = e

        raise last_error or Exception("Max retries exceeded")

    def list_servers(self, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
        """List servers with pagination."""
        url = f"{SMITHERY_API_URL}/servers"
        params = {"page": page, "pageSize": page_size}
        return self._request_with_retry(url, params)

    def get_server(self, qualified_name: str) -> dict:
        """Get detailed server info including tools."""
        url = f"{SMITHERY_API_URL}/servers/{qualified_name}"
        return self._request_with_retry(url)


def save_server(server_data: dict, servers_dir: Path) -> str:
    """Save server to file. Returns qualified name."""
    qualified_name = server_data.get("qualifiedName", "unknown")

    # Create safe filename
    safe_name = qualified_name.replace("/", "__").replace("@", "_at_")
    filename = f"{safe_name}.json"

    # Add sync metadata
    full_data = {
        **server_data,
        "_sync": {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "smithery"
        }
    }

    filepath = servers_dir / filename
    with open(filepath, "w") as f:
        json.dump(full_data, f, indent=2)

    return qualified_name


def build_index(servers_dir: Path) -> dict:
    """Build index of all servers."""
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "smithery",
        "total_count": 0,
        "servers": []
    }

    for filepath in sorted(servers_dir.glob("*.json")):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            tools = data.get("tools") or []  # Handle None case

            index["servers"].append({
                "qualified_name": data.get("qualifiedName"),
                "display_name": data.get("displayName"),
                "description": data.get("description"),
                "icon_url": data.get("iconUrl"),
                "verified": data.get("verified", False),
                "use_count": data.get("useCount", 0),
                "remote": data.get("remote", False),
                "deployment_url": data.get("deploymentUrl"),
                "tool_count": len(tools),
                "tool_names": [t.get("name") for t in tools],
                "created_at": data.get("createdAt"),
                "file": filepath.name
            })
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to index {filepath}: {e}")

    index["total_count"] = len(index["servers"])
    return index


def pull_servers(
    full_sync: bool = False,
    limit: Optional[int] = None,
    api_key: Optional[str] = None
) -> int:
    """Pull servers from Smithery."""
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)

    # Get API key from environment
    key = api_key or os.environ.get("SMITHERY_API_KEY") or os.environ.get("SMITHERY_BEARER_AUTH")
    if not key:
        logger.error("No API key provided. Set SMITHERY_API_KEY in .env or pass --api-key")
        return 0

    state = SyncState(STATE_FILE)
    client = SmitheryClient(key)

    start_time = time.time()
    servers_fetched = 0

    # Get total count first
    try:
        first_page = client.list_servers(page=1, page_size=1)
        pagination = first_page.get("pagination", {})
        total_pages = pagination.get("totalPages", 1)
        total_count = pagination.get("totalCount", 0)
        logger.info(f"Smithery has {total_count} servers across {total_pages} pages")
    except Exception as e:
        logger.error(f"Failed to connect to Smithery: {e}")
        return 0

    sync_start = datetime.now(timezone.utc).isoformat()

    try:
        page = 1
        while page <= total_pages:
            logger.info(f"Fetching page {page}/{total_pages}...")

            # Get list of servers (basic info)
            response = client.list_servers(page=page, page_size=DEFAULT_PAGE_SIZE)
            servers = response.get("servers", [])

            if not servers:
                break

            # Fetch full details for each server (includes tools)
            for server_basic in servers:
                qualified_name = server_basic.get("qualifiedName")

                try:
                    # Get full server details with tools
                    server_full = client.get_server(qualified_name)
                    save_server(server_full, SERVERS_DIR)
                    servers_fetched += 1

                    tool_count = len(server_full.get("tools") or [])
                    if servers_fetched % 50 == 0:
                        logger.info(f"Progress: {servers_fetched} servers saved")

                    # Check limit
                    if limit and servers_fetched >= limit:
                        logger.info(f"Reached limit of {limit}")
                        break

                except Exception as e:
                    logger.warning(f"Failed to fetch {qualified_name}: {e}")

                # Small delay to be nice
                time.sleep(0.05)

            if limit and servers_fetched >= limit:
                break

            page += 1

    except KeyboardInterrupt:
        logger.warning("Interrupted. Saving progress...")

    finally:
        duration = time.time() - start_time

        # Update state
        state.state["last_sync"] = sync_start
        state.state["last_page"] = page
        state.state["total_servers"] = len(list(SERVERS_DIR.glob("*.json")))
        state.state["sync_history"].append({
            "timestamp": sync_start,
            "servers_fetched": servers_fetched,
            "duration_seconds": round(duration, 2)
        })
        state.state["sync_history"] = state.state["sync_history"][-100:]
        state.save()

        # Build index
        logger.info("Building index...")
        index = build_index(SERVERS_DIR)
        with open(INDEX_FILE, "w") as f:
            json.dump(index, f, indent=2)

        logger.info(f"Sync complete: {servers_fetched} servers in {duration:.1f}s")
        logger.info(f"Total on disk: {state.state['total_servers']}")

    return servers_fetched


def main():
    parser = argparse.ArgumentParser(description="Pull MCP servers from Smithery")
    parser.add_argument("--full", action="store_true", help="Force full re-sync")
    parser.add_argument("--limit", type=int, help="Limit servers to fetch")
    parser.add_argument("--api-key", type=str, help="Smithery API key")
    parser.add_argument("--quiet", action="store_true", help="Less output")

    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    try:
        count = pull_servers(
            full_sync=args.full,
            limit=args.limit,
            api_key=args.api_key
        )
        sys.exit(0 if count > 0 else 1)
    except Exception as e:
        logger.error(f"Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
