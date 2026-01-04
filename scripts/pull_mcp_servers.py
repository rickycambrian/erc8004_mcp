#!/usr/bin/env python3
"""
Incremental MCP Server Registry Puller

This script fetches all MCP servers from the official registry at
https://registry.modelcontextprotocol.io and stores them locally.

Features:
- Incremental updates using cursor-based pagination
- Tracks last successful sync timestamp for future runs
- Stores individual server files and a combined index
- Handles rate limiting and network errors gracefully
- Produces production-ready JSON output

Usage:
    python pull_mcp_servers.py [--full] [--limit N]

    --full    Force a full re-sync instead of incremental
    --limit   Limit number of servers to fetch (for testing)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests

# Configuration
REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io"
API_VERSION = "v0"
DEFAULT_PAGE_LIMIT = 100  # Max allowed by API
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Paths
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
SERVERS_DIR = DATA_DIR / "servers"
STATE_FILE = DATA_DIR / "sync_state.json"
INDEX_FILE = DATA_DIR / "servers_index.json"
FULL_EXPORT_FILE = DATA_DIR / "all_servers.json"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class SyncState:
    """Manages synchronization state for incremental updates."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state = self._load()

    def _load(self) -> dict:
        """Load state from file or return default."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load state file: {e}. Starting fresh.")

        return {
            "last_sync": None,
            "last_cursor": None,
            "total_servers": 0,
            "sync_history": []
        }

    def save(self):
        """Persist state to file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    @property
    def last_sync(self) -> Optional[str]:
        return self.state.get("last_sync")

    @last_sync.setter
    def last_sync(self, value: str):
        self.state["last_sync"] = value

    @property
    def last_cursor(self) -> Optional[str]:
        return self.state.get("last_cursor")

    @last_cursor.setter
    def last_cursor(self, value: Optional[str]):
        self.state["last_cursor"] = value

    @property
    def total_servers(self) -> int:
        return self.state.get("total_servers", 0)

    @total_servers.setter
    def total_servers(self, value: int):
        self.state["total_servers"] = value

    def add_sync_record(self, servers_fetched: int, duration_seconds: float):
        """Record a sync operation."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "servers_fetched": servers_fetched,
            "duration_seconds": round(duration_seconds, 2)
        }
        history = self.state.get("sync_history", [])
        history.append(record)
        # Keep last 100 sync records
        self.state["sync_history"] = history[-100:]


class MCPRegistryClient:
    """Client for the MCP Registry API."""

    def __init__(self, base_url: str = REGISTRY_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ERC8004-MCP-Sync/1.0"
        })

    def _request_with_retry(self, url: str, params: dict = None) -> dict:
        """Make a request with retry logic."""
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()
                return response.json()

            except requests.exceptions.HTTPError as e:
                if response.status_code == 429:
                    # Rate limited - wait longer
                    wait_time = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    last_error = e
                elif response.status_code >= 500:
                    # Server error - retry
                    wait_time = RETRY_DELAY * (attempt + 1)
                    logger.warning(f"Server error {response.status_code}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    last_error = e
                else:
                    raise

            except requests.exceptions.RequestException as e:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"Request failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                last_error = e

        raise last_error or Exception("Max retries exceeded")

    def list_servers(
        self,
        cursor: str = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        updated_since: str = None,
        search: str = None
    ) -> dict:
        """
        List MCP servers with pagination.

        Args:
            cursor: Pagination cursor from previous response
            limit: Number of results per page (max 100)
            updated_since: RFC3339 timestamp to filter by update time
            search: Substring to search in server names

        Returns:
            API response with 'servers' and 'metadata' keys
        """
        url = f"{self.base_url}/{API_VERSION}/servers"
        params = {"limit": min(limit, 100)}

        if cursor:
            params["cursor"] = cursor
        if updated_since:
            params["updated_since"] = updated_since
        if search:
            params["search"] = search

        return self._request_with_retry(url, params)

    def get_server(self, server_name: str, version: str = "latest") -> dict:
        """Get a specific server by name and version."""
        # URL encode the server name (contains /)
        encoded_name = requests.utils.quote(server_name, safe="")
        encoded_version = requests.utils.quote(version, safe="")
        url = f"{self.base_url}/{API_VERSION}/servers/{encoded_name}/versions/{encoded_version}"
        return self._request_with_retry(url)


def save_server(server_data: dict, servers_dir: Path) -> str:
    """
    Save a server to its own file.

    Returns the server's unique identifier.
    """
    server = server_data.get("server", {})
    meta = server_data.get("_meta", {})

    name = server.get("name", "unknown")
    version = server.get("version", "unknown")

    # Create safe filename from name (replace / with __)
    safe_name = name.replace("/", "__")
    filename = f"{safe_name}__{version}.json"

    # Combine server data with registry metadata
    full_data = {
        "server": server,
        "_meta": meta,
        "_sync": {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "registry.modelcontextprotocol.io"
        }
    }

    filepath = servers_dir / filename
    with open(filepath, "w") as f:
        json.dump(full_data, f, indent=2)

    return f"{name}:{version}"


def build_index(servers_dir: Path) -> dict:
    """Build an index of all downloaded servers."""
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_count": 0,
        "servers": []
    }

    for filepath in sorted(servers_dir.glob("*.json")):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            server = data.get("server", {})
            meta = data.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})

            index["servers"].append({
                "name": server.get("name"),
                "version": server.get("version"),
                "description": server.get("description"),
                "title": server.get("title"),
                "status": meta.get("status", "unknown"),
                "is_latest": meta.get("isLatest", False),
                "published_at": meta.get("publishedAt"),
                "has_remotes": bool(server.get("remotes")),
                "has_packages": bool(server.get("packages")),
                "file": filepath.name
            })
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to index {filepath}: {e}")

    index["total_count"] = len(index["servers"])
    return index


def build_full_export(servers_dir: Path) -> list:
    """Build a complete export of all server data."""
    all_servers = []

    for filepath in sorted(servers_dir.glob("*.json")):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            all_servers.append(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    return all_servers


def pull_servers(
    full_sync: bool = False,
    limit: Optional[int] = None,
    updated_since: Optional[str] = None
) -> int:
    """
    Pull servers from the registry.

    Args:
        full_sync: If True, ignore previous state and do full sync
        limit: Maximum number of servers to fetch (None = all)
        updated_since: Only fetch servers updated after this timestamp

    Returns:
        Number of servers fetched
    """
    # Initialize
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    state = SyncState(STATE_FILE)
    client = MCPRegistryClient()

    start_time = time.time()
    servers_fetched = 0
    cursor = None

    # Determine sync mode
    if full_sync:
        logger.info("Starting FULL sync (ignoring previous state)")
        sync_timestamp = None
    elif updated_since:
        logger.info(f"Syncing servers updated since {updated_since}")
        sync_timestamp = updated_since
    elif state.last_sync:
        logger.info(f"Incremental sync since {state.last_sync}")
        sync_timestamp = state.last_sync
    else:
        logger.info("No previous sync found. Doing full sync.")
        sync_timestamp = None

    sync_start = datetime.now(timezone.utc).isoformat()

    try:
        while True:
            # Fetch a page
            logger.info(f"Fetching page (cursor: {cursor[:50] if cursor else 'start'})...")

            response = client.list_servers(
                cursor=cursor,
                limit=DEFAULT_PAGE_LIMIT,
                updated_since=sync_timestamp
            )

            servers = response.get("servers", [])
            metadata = response.get("metadata", {})

            if not servers:
                logger.info("No more servers to fetch.")
                break

            # Save each server
            for server_data in servers:
                server_id = save_server(server_data, SERVERS_DIR)
                servers_fetched += 1

                if servers_fetched % 100 == 0:
                    logger.info(f"Progress: {servers_fetched} servers saved")

                # Check limit
                if limit and servers_fetched >= limit:
                    logger.info(f"Reached limit of {limit} servers")
                    break

            # Check if we've hit the limit
            if limit and servers_fetched >= limit:
                break

            # Get next cursor
            cursor = metadata.get("nextCursor")
            if not cursor:
                logger.info("Reached end of pagination.")
                break

            # Small delay to be nice to the server
            time.sleep(0.1)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Saving progress...")

    except Exception as e:
        logger.error(f"Error during sync: {e}")
        raise

    finally:
        # Update state
        duration = time.time() - start_time

        if servers_fetched > 0:
            state.last_sync = sync_start
            state.last_cursor = cursor
            state.add_sync_record(servers_fetched, duration)

        # Count total servers on disk
        state.total_servers = len(list(SERVERS_DIR.glob("*.json")))
        state.save()

        # Build index
        logger.info("Building server index...")
        index = build_index(SERVERS_DIR)
        with open(INDEX_FILE, "w") as f:
            json.dump(index, f, indent=2)

        # Build full export
        logger.info("Building full export...")
        all_servers = build_full_export(SERVERS_DIR)
        with open(FULL_EXPORT_FILE, "w") as f:
            json.dump({
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "total_count": len(all_servers),
                "servers": all_servers
            }, f, indent=2)

        logger.info(f"Sync complete: {servers_fetched} servers fetched in {duration:.1f}s")
        logger.info(f"Total servers on disk: {state.total_servers}")
        logger.info(f"Index saved to: {INDEX_FILE}")
        logger.info(f"Full export saved to: {FULL_EXPORT_FILE}")

    return servers_fetched


def main():
    parser = argparse.ArgumentParser(
        description="Pull MCP servers from the official registry"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full re-sync instead of incremental"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of servers to fetch (for testing)"
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only fetch servers updated since this timestamp (RFC3339)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity"
    )

    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    try:
        count = pull_servers(
            full_sync=args.full,
            limit=args.limit,
            updated_since=args.since
        )

        if count == 0:
            logger.info("No new servers to sync.")

        sys.exit(0)

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
