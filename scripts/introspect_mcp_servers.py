#!/usr/bin/env python3
"""
MCP Server Introspection Script

This script connects to MCP servers and extracts their capabilities:
- tools/list - Available tools
- prompts/list - Available prompts
- resources/list - Available resources

Features:
- Supports streamable-http, sse, and stdio transports
- Incremental processing (tracks which servers have been introspected)
- Stores introspection results alongside server data
- Handles timeouts and connection failures gracefully
- Produces enriched server data with capabilities

Usage:
    python introspect_mcp_servers.py [--limit N] [--force] [--filter PATTERN]

    --limit N       Only introspect N servers
    --force         Re-introspect servers even if already done
    --filter PATTERN  Only introspect servers matching pattern
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

# Configuration
REQUEST_TIMEOUT = 30  # seconds
MAX_CONCURRENT = 10   # max concurrent introspections
RETRY_COUNT = 2

# Paths
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
SERVERS_DIR = DATA_DIR / "servers"
INTROSPECTION_DIR = DATA_DIR / "introspection"
INTROSPECTION_STATE_FILE = DATA_DIR / "introspection_state.json"
ENRICHED_EXPORT_FILE = DATA_DIR / "servers_enriched.json"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class IntrospectionState:
    """Tracks which servers have been introspected."""

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
            "introspected": {},  # server_id -> timestamp
            "failed": {},        # server_id -> error message
            "stats": {
                "total_success": 0,
                "total_failed": 0,
                "total_skipped": 0
            }
        }

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def is_introspected(self, server_id: str) -> bool:
        return server_id in self.state["introspected"]

    def mark_success(self, server_id: str):
        self.state["introspected"][server_id] = datetime.now(timezone.utc).isoformat()
        if server_id in self.state["failed"]:
            del self.state["failed"][server_id]
        self.state["stats"]["total_success"] += 1

    def mark_failed(self, server_id: str, error: str):
        self.state["failed"][server_id] = {
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.state["stats"]["total_failed"] += 1

    def mark_skipped(self, server_id: str, reason: str):
        self.state["stats"]["total_skipped"] += 1


class MCPIntrospector:
    """Introspects MCP servers to extract their capabilities."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _make_jsonrpc_request(
        self,
        url: str,
        method: str,
        params: dict = None,
        headers: dict = None
    ) -> dict:
        """Make a JSON-RPC 2.0 request."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {}
        }

        # MCP servers require both JSON and SSE accept headers
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        if headers:
            request_headers.update(headers)

        async with self.session.post(url, json=payload, headers=request_headers) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"HTTP {response.status}: {text[:200]}")

            # Handle both JSON and SSE responses
            content_type = response.headers.get("Content-Type", "")

            if "text/event-stream" in content_type:
                # Parse SSE response - look for data: lines
                text = await response.text()
                for line in text.split("\n"):
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                            if "result" in data:
                                return data.get("result", {})
                        except json.JSONDecodeError:
                            continue
                raise Exception("No valid JSON-RPC response in SSE stream")
            else:
                data = await response.json()

                if "error" in data:
                    error = data["error"]
                    raise Exception(f"RPC Error {error.get('code')}: {error.get('message')}")

                return data.get("result", {})

    async def _try_streamable_http(self, url: str, headers: dict = None) -> dict:
        """Try streamable-http transport (POST with JSON-RPC)."""
        result = {
            "tools": [],
            "prompts": [],
            "resources": [],
            "transport": "streamable-http",
            "errors": []
        }

        # Try to get tools
        try:
            tools_result = await self._make_jsonrpc_request(url, "tools/list", headers=headers)
            result["tools"] = tools_result.get("tools", [])
        except Exception as e:
            error_str = str(e)[:200]
            logger.debug(f"tools/list failed: {error_str}")
            result["errors"].append(f"tools/list: {error_str}")

        # Try to get prompts
        try:
            prompts_result = await self._make_jsonrpc_request(url, "prompts/list", headers=headers)
            result["prompts"] = prompts_result.get("prompts", [])
        except Exception as e:
            error_str = str(e)[:200]
            logger.debug(f"prompts/list failed: {error_str}")
            result["errors"].append(f"prompts/list: {error_str}")

        # Try to get resources
        try:
            resources_result = await self._make_jsonrpc_request(url, "resources/list", headers=headers)
            result["resources"] = resources_result.get("resources", [])
        except Exception as e:
            error_str = str(e)[:200]
            logger.debug(f"resources/list failed: {error_str}")
            result["errors"].append(f"resources/list: {error_str}")

        return result

    async def _try_sse(self, url: str, headers: dict = None) -> dict:
        """Try SSE transport."""
        # SSE endpoints typically have a different flow
        # For now, we'll try the same JSON-RPC approach
        return await self._try_streamable_http(url, headers)

    async def introspect(self, server_data: dict) -> dict:
        """
        Introspect a server and return its capabilities.

        Args:
            server_data: The server data from the registry

        Returns:
            Dict with tools, prompts, resources, and metadata
        """
        server = server_data.get("server", {})
        name = server.get("name", "unknown")
        remotes = server.get("remotes", [])
        packages = server.get("packages", [])

        result = {
            "server_name": name,
            "server_version": server.get("version"),
            "introspected_at": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "tools": [],
            "prompts": [],
            "resources": [],
            "transport_used": None,
            "error": None
        }

        # Try remote endpoints first (they're directly accessible)
        for remote in remotes:
            transport_type = remote.get("type")
            url = remote.get("url")

            if not url:
                continue

            logger.debug(f"Trying {transport_type} at {url}")

            try:
                headers = {}
                for header in remote.get("headers", []):
                    header_name = header.get("name")
                    header_value = header.get("value")
                    if header_name and header_value and not header.get("isSecret"):
                        headers[header_name] = header_value

                if transport_type == "streamable-http":
                    caps = await self._try_streamable_http(url, headers)
                elif transport_type == "sse":
                    caps = await self._try_sse(url, headers)
                else:
                    continue

                if caps.get("tools") or caps.get("prompts") or caps.get("resources"):
                    result["tools"] = caps.get("tools", [])
                    result["prompts"] = caps.get("prompts", [])
                    result["resources"] = caps.get("resources", [])
                    result["transport_used"] = transport_type
                    result["endpoint_url"] = url
                    result["success"] = True
                    return result
                else:
                    # All requests failed - capture the errors
                    errors = caps.get("errors", [])
                    if errors:
                        result["error"] = errors[0] if len(errors) == 1 else "; ".join(errors[:2])
                    result["endpoint_url"] = url

            except asyncio.TimeoutError:
                logger.debug(f"Timeout connecting to {url}")
                result["error"] = "timeout"
            except Exception as e:
                logger.debug(f"Failed to connect to {url}: {e}")
                result["error"] = str(e)[:200]

        # For stdio packages, we can't introspect remotely
        # Just note what packages are available
        if packages and not result["success"]:
            package_info = []
            for pkg in packages:
                package_info.append({
                    "registry": pkg.get("registryType"),
                    "identifier": pkg.get("identifier"),
                    "transport": pkg.get("transport", {}).get("type")
                })
            result["packages"] = package_info
            result["error"] = "stdio_only"

        return result


def load_server_files(servers_dir: Path, filter_pattern: str = None) -> list:
    """Load all server files from disk."""
    servers = []

    for filepath in sorted(servers_dir.glob("*.json")):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            server = data.get("server", {})
            name = server.get("name", "")

            # Apply filter if specified
            if filter_pattern and not re.search(filter_pattern, name, re.IGNORECASE):
                continue

            servers.append({
                "filepath": filepath,
                "data": data,
                "server_id": f"{name}:{server.get('version', 'unknown')}"
            })

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    return servers


def save_introspection_result(result: dict, introspection_dir: Path, server_id: str):
    """Save introspection result to file."""
    safe_id = server_id.replace("/", "__").replace(":", "__")
    filepath = introspection_dir / f"{safe_id}.json"

    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)


def build_enriched_export(servers_dir: Path, introspection_dir: Path) -> list:
    """Build enriched export combining server data with introspection results."""
    enriched = []

    for server_file in sorted(servers_dir.glob("*.json")):
        try:
            with open(server_file, "r") as f:
                server_data = json.load(f)

            server = server_data.get("server", {})
            name = server.get("name", "")
            version = server.get("version", "")
            server_id = f"{name}:{version}"

            # Look for introspection file
            safe_id = server_id.replace("/", "__").replace(":", "__")
            introspection_file = introspection_dir / f"{safe_id}.json"

            introspection = None
            if introspection_file.exists():
                with open(introspection_file, "r") as f:
                    introspection = json.load(f)

            enriched.append({
                "server": server,
                "_meta": server_data.get("_meta", {}),
                "_introspection": introspection
            })

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to process {server_file}: {e}")

    return enriched


async def introspect_servers(
    limit: Optional[int] = None,
    force: bool = False,
    filter_pattern: str = None
) -> dict:
    """
    Introspect MCP servers to extract their capabilities.

    Args:
        limit: Max number of servers to introspect
        force: Re-introspect even if already done
        filter_pattern: Only introspect servers matching pattern

    Returns:
        Stats about the introspection run
    """
    INTROSPECTION_DIR.mkdir(parents=True, exist_ok=True)
    state = IntrospectionState(INTROSPECTION_STATE_FILE)

    # Load servers
    servers = load_server_files(SERVERS_DIR, filter_pattern)
    logger.info(f"Found {len(servers)} servers to process")

    if not servers:
        logger.warning("No servers found. Run pull_mcp_servers.py first.")
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    # Filter already processed if not forcing
    if not force:
        servers = [s for s in servers if not state.is_introspected(s["server_id"])]
        logger.info(f"After filtering processed: {len(servers)} servers remaining")

    # Apply limit
    if limit:
        servers = servers[:limit]
        logger.info(f"Limited to {len(servers)} servers")

    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    async with MCPIntrospector() as introspector:
        # Process in batches to control concurrency
        for i in range(0, len(servers), MAX_CONCURRENT):
            batch = servers[i:i + MAX_CONCURRENT]

            tasks = []
            for server_entry in batch:
                server_data = server_entry["data"]
                server = server_data.get("server", {})

                # Skip servers without remote endpoints
                if not server.get("remotes"):
                    state.mark_skipped(server_entry["server_id"], "no_remotes")
                    stats["skipped"] += 1
                    continue

                tasks.append((
                    server_entry["server_id"],
                    introspector.introspect(server_data)
                ))

            # Execute batch
            for server_id, task in tasks:
                try:
                    result = await task
                    stats["processed"] += 1

                    # Save result
                    save_introspection_result(result, INTROSPECTION_DIR, server_id)

                    if result["success"]:
                        state.mark_success(server_id)
                        stats["success"] += 1
                        tool_count = len(result.get("tools", []))
                        logger.info(f"[OK] {server_id}: {tool_count} tools")
                    else:
                        state.mark_failed(server_id, result.get("error", "unknown"))
                        stats["failed"] += 1
                        logger.debug(f"[FAIL] {server_id}: {result.get('error')}")

                except Exception as e:
                    stats["processed"] += 1
                    stats["failed"] += 1
                    state.mark_failed(server_id, str(e))
                    logger.warning(f"[ERROR] {server_id}: {e}")

            # Progress update
            total = stats["processed"] + stats["skipped"]
            if total % 50 == 0:
                logger.info(f"Progress: {total} processed, {stats['success']} success, {stats['failed']} failed")

    # Save state
    state.save()

    # Build enriched export
    logger.info("Building enriched export...")
    enriched = build_enriched_export(SERVERS_DIR, INTROSPECTION_DIR)
    with open(ENRICHED_EXPORT_FILE, "w") as f:
        json.dump({
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_count": len(enriched),
            "servers": enriched
        }, f, indent=2)

    logger.info(f"Enriched export saved to: {ENRICHED_EXPORT_FILE}")
    logger.info(f"Stats: {stats}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Introspect MCP servers to extract tools, prompts, and resources"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of servers to introspect"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-introspect servers even if already done"
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="Only introspect servers matching this pattern (regex)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output"
    )

    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    elif args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        stats = asyncio.run(introspect_servers(
            limit=args.limit,
            force=args.force,
            filter_pattern=args.filter
        ))

        if stats["success"] == 0 and stats["processed"] > 0:
            logger.warning("No servers were successfully introspected.")
            logger.info("Note: Many MCP servers use stdio transport and cannot be introspected remotely.")

        sys.exit(0)

    except Exception as e:
        logger.error(f"Introspection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
