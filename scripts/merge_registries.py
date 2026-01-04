#!/usr/bin/env python3
"""
Multi-Registry MCP Server Merger

Merges data from multiple MCP server registries into a unified format:
- Official MCP Registry (registry.modelcontextprotocol.io)
- Smithery (registry.smithery.ai)
- Future: mcpso, awesome-mcp-servers, etc.

Features:
- Deduplicates servers across registries
- Normalizes to unified schema
- Tracks source attribution
- Prefers data with more tools/better quality

Usage:
    python merge_registries.py [--force]
"""

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

# Paths
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
SOURCES_DIR = DATA_DIR / "sources"
UNIFIED_DIR = DATA_DIR / "unified"
EXPORTS_DIR = DATA_DIR / "exports"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def normalize_server_name(name: str) -> str:
    """Normalize server name for deduplication."""
    # Remove common prefixes/suffixes
    name = name.lower().strip()

    # Remove version suffixes
    name = re.sub(r'[:\-_]v?\d+(\.\d+)*$', '', name)

    # Normalize separators
    name = re.sub(r'[-_/]+', '-', name)

    # Remove common suffixes
    for suffix in ['-mcp', '-server', '-mcp-server']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]

    return name


def extract_repo_name(url: str) -> Optional[str]:
    """Extract normalized repo name from GitHub URL."""
    if not url:
        return None

    # Match github.com/owner/repo patterns
    match = re.search(r'github\.com/([^/]+)/([^/\s]+)', url)
    if match:
        owner = match.group(1).lower()
        repo = match.group(2).lower()
        # Remove .git suffix
        repo = re.sub(r'\.git$', '', repo)
        return f"{owner}/{repo}"

    return None


def load_official_servers(source_dir: Path) -> List[Dict]:
    """Load servers from official registry."""
    servers = []
    servers_dir = source_dir / "servers"
    introspection_dir = source_dir / "introspection"

    if not servers_dir.exists():
        return servers

    for filepath in servers_dir.glob("*.json"):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            server = data.get("server", {})
            meta = data.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})

            # Load introspection if available
            introspection = None
            server_name = server.get("name", "")
            version = server.get("version", "")
            safe_id = f"{server_name}:{version}".replace("/", "__").replace(":", "__")
            intro_file = introspection_dir / f"{safe_id}.json"

            if intro_file.exists():
                try:
                    with open(intro_file, "r") as f:
                        introspection = json.load(f)
                except:
                    pass

            # Get tools from introspection
            tools = []
            if introspection and introspection.get("success"):
                tools = introspection.get("tools", [])

            # Extract GitHub repo
            repo_url = server.get("repository", {}).get("url")

            servers.append({
                "source": "official",
                "source_priority": 1,  # Lower priority (less rich data)
                "name": server.get("name"),
                "version": server.get("version"),
                "display_name": server.get("title") or server.get("name"),
                "description": server.get("description"),
                "icon_url": (server.get("icons") or [{}])[0].get("src") if server.get("icons") else None,
                "repository_url": repo_url,
                "repository_name": extract_repo_name(repo_url),
                "remote_endpoint": (server.get("remotes") or [{}])[0].get("url") if server.get("remotes") else None,
                "packages": server.get("packages", []),
                "tools": tools,
                "tool_count": len(tools),
                "prompts": introspection.get("prompts", []) if introspection else [],
                "resources": introspection.get("resources", []) if introspection else [],
                "is_latest": meta.get("isLatest", False),
                "status": meta.get("status", "unknown"),
                "published_at": meta.get("publishedAt"),
                "raw_data": data,
                "_normalized_name": normalize_server_name(server.get("name", ""))
            })

        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    return servers


def load_smithery_servers(source_dir: Path) -> List[Dict]:
    """Load servers from Smithery registry."""
    servers = []
    servers_dir = source_dir / "servers"

    if not servers_dir.exists():
        return servers

    for filepath in servers_dir.glob("*.json"):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            tools = data.get("tools") or []  # Handle None case

            # Extract deployment URL
            connections = data.get("connections") or []
            remote_endpoint = None
            for conn in connections:
                if conn.get("type") == "http":
                    remote_endpoint = conn.get("deploymentUrl")
                    break
            if not remote_endpoint:
                remote_endpoint = data.get("deploymentUrl")

            servers.append({
                "source": "smithery",
                "source_priority": 2,  # Higher priority (richer data with full tools)
                "name": data.get("qualifiedName"),
                "version": None,  # Smithery doesn't version
                "display_name": data.get("displayName"),
                "description": data.get("description"),
                "icon_url": data.get("iconUrl"),
                "repository_url": None,  # Smithery doesn't provide repo
                "repository_name": None,
                "remote_endpoint": remote_endpoint,
                "packages": [],
                "tools": tools,
                "tool_count": len(tools),
                "prompts": [],
                "resources": [],
                "is_latest": True,
                "status": "active",
                "published_at": data.get("createdAt"),
                "verified": data.get("verified", False),
                "use_count": data.get("useCount", 0),
                "raw_data": data,
                "_normalized_name": normalize_server_name(data.get("qualifiedName", ""))
            })

        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    return servers


def deduplicate_servers(all_servers: List[Dict]) -> List[Dict]:
    """
    Deduplicate servers across registries.

    Strategy:
    1. Group by normalized name
    2. If same server in multiple sources, prefer the one with more tools
    3. Keep all versions from official registry but only latest
    """
    # Group by normalized name
    by_name = defaultdict(list)
    for server in all_servers:
        key = server["_normalized_name"]
        by_name[key].append(server)

    deduplicated = []
    duplicates_found = 0

    for name, group in by_name.items():
        if len(group) == 1:
            deduplicated.append(group[0])
            continue

        # Multiple servers with same normalized name
        duplicates_found += 1

        # Separate by source
        by_source = defaultdict(list)
        for s in group:
            by_source[s["source"]].append(s)

        # Pick the best one
        # Prefer: 1) Most tools 2) Higher priority source 3) Latest version
        best = max(group, key=lambda s: (
            s["tool_count"],
            s["source_priority"],
            1 if s["is_latest"] else 0
        ))

        # Track that this was deduplicated
        best["_sources"] = list(by_source.keys())
        best["_duplicate_count"] = len(group)

        deduplicated.append(best)

    logger.info(f"Deduplication: {len(all_servers)} -> {len(deduplicated)} servers ({duplicates_found} duplicates resolved)")

    return deduplicated


def create_unified_schema(server: Dict) -> Dict:
    """Convert to unified output schema."""
    return {
        # Identity
        "id": server["name"],
        "name": server["name"],
        "display_name": server["display_name"],
        "version": server["version"],

        # Metadata
        "description": server["description"],
        "icon_url": server["icon_url"],
        "repository_url": server["repository_url"],
        "status": server["status"],
        "published_at": server["published_at"],

        # Capabilities
        "tools": server["tools"],
        "tool_count": server["tool_count"],
        "prompts": server["prompts"],
        "resources": server["resources"],

        # Connectivity
        "remote_endpoint": server["remote_endpoint"],
        "packages": server["packages"],

        # Source tracking
        "sources": server.get("_sources", [server["source"]]),
        "primary_source": server["source"],
        "is_latest": server["is_latest"],

        # Quality indicators
        "verified": server.get("verified", False),
        "use_count": server.get("use_count", 0),

        # Derived
        "has_remote": bool(server["remote_endpoint"]),
        "has_tools": server["tool_count"] > 0,
        "tool_names": [t.get("name") for t in server["tools"]]
    }


def merge_all_registries(force: bool = False):
    """Merge all registry sources into unified data."""
    UNIFIED_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    all_servers = []
    source_stats = {}

    # Load from each source
    for source_name in ["official", "smithery"]:
        source_dir = SOURCES_DIR / source_name

        if not source_dir.exists():
            logger.info(f"Skipping {source_name}: directory not found")
            continue

        logger.info(f"Loading from {source_name}...")

        if source_name == "official":
            servers = load_official_servers(source_dir)
        elif source_name == "smithery":
            servers = load_smithery_servers(source_dir)
        else:
            continue

        source_stats[source_name] = {
            "total": len(servers),
            "with_tools": sum(1 for s in servers if s["tool_count"] > 0),
            "total_tools": sum(s["tool_count"] for s in servers)
        }

        logger.info(f"  {source_name}: {len(servers)} servers, {source_stats[source_name]['with_tools']} with tools")
        all_servers.extend(servers)

    # Deduplicate
    logger.info("Deduplicating...")
    deduplicated = deduplicate_servers(all_servers)

    # Convert to unified schema
    unified = [create_unified_schema(s) for s in deduplicated]

    # Sort by tool count (most useful first)
    unified.sort(key=lambda s: (-s["tool_count"], s["name"]))

    # Save unified index
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(unified),
        "with_tools": sum(1 for s in unified if s["has_tools"]),
        "total_tools": sum(s["tool_count"] for s in unified),
        "sources": source_stats,
        "servers": [{
            "id": s["id"],
            "name": s["name"],
            "display_name": s["display_name"],
            "description": s["description"][:200] if s["description"] else None,
            "tool_count": s["tool_count"],
            "sources": s["sources"],
            "has_remote": s["has_remote"]
        } for s in unified]
    }

    with open(UNIFIED_DIR / "index.json", "w") as f:
        json.dump(index, f, indent=2)

    # Save individual server files
    servers_dir = UNIFIED_DIR / "servers"
    servers_dir.mkdir(exist_ok=True)

    for server in unified:
        safe_name = server["name"].replace("/", "__").replace(":", "__")
        filepath = servers_dir / f"{safe_name}.json"
        with open(filepath, "w") as f:
            json.dump(server, f, indent=2)

    # Create exports
    # Full export
    full_export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(unified),
        "servers": unified
    }
    with open(EXPORTS_DIR / "all_servers_unified.json", "w") as f:
        json.dump(full_export, f, indent=2)

    # Servers with tools only
    with_tools = [s for s in unified if s["has_tools"]]
    tools_export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(with_tools),
        "total_tools": sum(s["tool_count"] for s in with_tools),
        "servers": with_tools
    }
    with open(EXPORTS_DIR / "servers_with_tools.json", "w") as f:
        json.dump(tools_export, f, indent=2)

    # Summary
    logger.info("=" * 60)
    logger.info("MERGE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total unified servers: {len(unified)}")
    logger.info(f"Servers with tools: {len(with_tools)}")
    logger.info(f"Total tools: {sum(s['tool_count'] for s in unified)}")
    logger.info("")
    for source, stats in source_stats.items():
        logger.info(f"{source}: {stats['total']} servers, {stats['with_tools']} with tools")
    logger.info("")
    logger.info(f"Exports saved to: {EXPORTS_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Merge MCP server registries")
    parser.add_argument("--force", action="store_true", help="Force re-merge")
    parser.add_argument("--quiet", action="store_true", help="Less output")

    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    merge_all_registries(force=args.force)


if __name__ == "__main__":
    main()
