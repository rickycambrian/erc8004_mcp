# ERC-8004 MCP Server Registry

Register MCP servers on-chain via ERC-8004 using the Agent0 SDK.

## Overview

This project:
1. Pulls MCP server metadata from registries (Smithery, Anthropic MCP Registry)
2. Transforms the data to ERC-8004 registration format
3. Uploads to IPFS and registers on-chain (Base Sepolia)

## Setup

```bash
npm install
cp .env.example .env  # Add your keys
```

Required environment variables:
```
PRIVATE_KEY=0x...          # Wallet for signing transactions
PINATA_JWT=...             # Pinata JWT for IPFS pinning
SMITHERY_API_KEY=...       # Smithery registry API key
```

## Data Sources

### Pull from Smithery

```bash
python scripts/pull_smithery.py
```

Fetches ~3,200+ servers to `data/sources/smithery/servers/`.

### Pull from Anthropic MCP Registry

```bash
python scripts/pull_anthropic.py
```

Fetches ~3,100+ servers to `data/sources/anthropic/servers/`.

## Registration

### Single Server

```bash
# Dry run
npx tsx scripts/register-mcp.ts exa --dry-run

# Register
npx tsx scripts/register-mcp.ts exa
```

### Batch Registration

Incrementally register servers, avoiding duplicates:

```bash
# Preview what would be registered
npx tsx scripts/register-batch.ts --limit=10 --dry-run

# Register from Smithery only, with tools
npx tsx scripts/register-batch.ts --limit=5 --registry=smithery --tools-only

# Register from Anthropic registry, require endpoint
npx tsx scripts/register-batch.ts --limit=5 --registry=anthropic --require-endpoint

# Register all (both registries)
npx tsx scripts/register-batch.ts --limit=20 --require-endpoint
```

Options:
- `--limit=N` - Max servers to register (default: 10)
- `--registry=smithery|anthropic` - Filter by source
- `--tools-only` - Only servers with tools defined
- `--require-endpoint` - Only servers with MCP endpoint
- `--dry-run` - Preview without registering

State is tracked in `data/registration-state.json` to avoid duplicates and detect updates.

## Data Mapping

### Source Data (Smithery)

```json
{
  "qualifiedName": "exa",
  "displayName": "Exa Search",
  "description": "Fast web search...",
  "deploymentUrl": "https://server.smithery.ai/exa",
  "tools": [{"name": "web_search_exa"}],
  "connections": [{
    "configSchema": {
      "properties": {
        "exaApiKey": {"type": "string"}
      }
    }
  }]
}
```

### ERC-8004 Registration (IPFS)

```json
{
  "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
  "name": "Exa Search",
  "description": "Fast web search...",
  "endpoints": [{
    "name": "MCP",
    "endpoint": "https://server.smithery.ai/exa",
    "version": "2025-06-18",
    "mcpTools": ["web_search_exa"],
    "configSchema": {"properties": {"exaApiKey": {...}}},
    "npmPackage": "exa-mcp-server"
  }],
  "active": true
}
```

Key mappings:
| Source Field | ERC-8004 Field |
|--------------|----------------|
| `displayName` | `name` |
| `description` | `description` |
| `deploymentUrl` | `endpoints[].endpoint` |
| `tools[].name` | `endpoints[].mcpTools` |
| `connections[].configSchema` | `endpoints[].configSchema` |
| Derived from `qualifiedName` | `endpoints[].npmPackage` |

## Testing a Registered Server

### Local Execution (Recommended)

Uses `npmPackage` from registration metadata:

```bash
npx tsx scripts/test-mcp-local.ts exa
```

This spawns the server locally via stdio transport, bypassing OAuth.

### Remote Execution

Smithery-hosted servers require OAuth. See `scripts/test-mcp-client.ts` for the pattern.

## Project Structure

```
scripts/
  pull_smithery.py      # Fetch from Smithery registry
  pull_anthropic.py      # Fetch from Anthropic MCP registry
  register-mcp.ts       # Register single server on ERC-8004
  register-batch.ts     # Batch register with deduplication
  test-mcp-local.ts     # Test via local stdio
  test-mcp-client.ts    # Test via remote HTTP

data/
  sources/
    smithery/servers/   # Raw Smithery data (~3,200 servers)
    anthropic/servers/   # Raw Anthropic registry data (~3,100 servers)
  registrations/        # On-chain registration results
  registration-state.json  # Tracking for incremental sync
```

## Agent0 SDK Usage

```typescript
import { SDK } from 'agent0-sdk';

const sdk = new SDK({
  chainId: 84532,  // Base Sepolia
  rpcUrl: 'https://sepolia.base.org',
  signer: privateKey,
  ipfs: 'pinata',
  pinataJwt: pinataJwt
});

const agent = sdk.createAgent('Exa Search', 'Fast web search...');
await agent.setMCP('https://server.smithery.ai/exa', '2025-06-18', false);

// Add metadata for local execution
const regFile = agent.getRegistrationFile();
regFile.endpoints[0].meta = {
  mcpTools: ['web_search_exa'],
  configSchema: {...},
  npmPackage: 'exa-mcp-server'
};

const result = await agent.registerIPFS();
// result.agentId = "84532:2122"
// result.agentURI = "ipfs://..."
```
