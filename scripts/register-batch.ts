/**
 * Batch Register MCP Servers as ERC-8004 Agents
 *
 * Incrementally registers MCP servers from collected data onto ERC-8004.
 * Tracks registrations to avoid duplicates and supports updates.
 *
 * Usage:
 *   npx tsx scripts/register-batch.ts [--limit N] [--registry smithery|anthropic] [--dry-run]
 *
 * Environment:
 *   PRIVATE_KEY - Wallet private key
 *   PINATA_JWT - Pinata JWT for IPFS
 *   THE_GRAPH_API_KEY - The Graph API key for queries
 */

import { SDK } from 'agent0-sdk';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import * as crypto from 'crypto';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const CHAIN_ID = 84532;
const DEFAULT_RPC_URL = 'https://sepolia.base.org';
const MCP_VERSION = '2025-06-18';
const SUBGRAPH_ID = 'GjQEDgEKqoh5Yc8MUgxoQoRATEJdEiH7HbocfR1aFiHa';

// Data paths
const SMITHERY_DIR = path.join(__dirname, '../data/sources/smithery/servers');
const ANTHROPIC_DIR = path.join(__dirname, '../data/sources/anthropic/servers');
const REGISTRATIONS_DIR = path.join(__dirname, '../data/registrations');
const STATE_FILE = path.join(__dirname, '../data/registration-state.json');

// Types
interface ServerData {
  id: string;
  source: 'smithery' | 'anthropic';
  name: string;
  displayName: string;
  description: string;
  iconUrl?: string;
  mcpEndpoint?: string;
  npmPackage?: string;
  tools: string[];
  configSchema?: Record<string, unknown>;
  contentHash: string;
}

interface RegistrationState {
  registered: Record<string, {
    agentId: string;
    contentHash: string;
    registeredAt: string;
  }>;
}

/**
 * Load Smithery server data
 */
function loadSmitheryServer(filepath: string): ServerData | null {
  try {
    const data = JSON.parse(fs.readFileSync(filepath, 'utf-8'));

    if (!data.qualifiedName || !data.displayName) return null;

    const tools = (data.tools || []).map((t: { name: string }) => t.name).filter(Boolean);

    // Get MCP endpoint
    let mcpEndpoint = data.deploymentUrl;
    if (data.connections) {
      for (const conn of data.connections) {
        if (conn.type === 'http' && conn.deploymentUrl) {
          mcpEndpoint = conn.deploymentUrl;
          break;
        }
      }
    }
    if (!mcpEndpoint && data.remote && data.qualifiedName) {
      mcpEndpoint = `https://server.smithery.ai/${data.qualifiedName}`;
    }

    if (!mcpEndpoint) return null;

    // Get config schema
    let configSchema: Record<string, unknown> | undefined;
    if (data.connections) {
      for (const conn of data.connections) {
        if (conn.configSchema) {
          configSchema = conn.configSchema;
          break;
        }
      }
    }

    // Derive npm package
    const npmPackage = data.qualifiedName && !data.qualifiedName.includes('/')
      ? `${data.qualifiedName}-mcp-server`
      : undefined;

    const content = JSON.stringify({ name: data.displayName, description: data.description, tools, mcpEndpoint });
    const contentHash = crypto.createHash('sha256').update(content).digest('hex').slice(0, 16);

    return {
      id: `smithery:${data.qualifiedName}`,
      source: 'smithery',
      name: data.qualifiedName,
      displayName: data.displayName,
      description: data.description || '',
      iconUrl: data.iconUrl,
      mcpEndpoint,
      npmPackage,
      tools,
      configSchema,
      contentHash
    };
  } catch {
    return null;
  }
}

/**
 * Load Anthropic registry server data
 */
function loadAnthropicServer(filepath: string): ServerData | null {
  try {
    const data = JSON.parse(fs.readFileSync(filepath, 'utf-8'));

    if (!data.server?.name) return null;

    const server = data.server;
    const publisherMeta = server._meta?.['io.modelcontextprotocol.registry/publisher-provided'];

    // Get tools from publisher metadata
    const tools = (publisherMeta?.tools || []).map((t: { name: string }) => t.name).filter(Boolean);

    // Get MCP endpoint from remotes
    let mcpEndpoint: string | undefined;
    if (server.remotes) {
      for (const remote of server.remotes) {
        if (remote.url) {
          mcpEndpoint = remote.url;
          break;
        }
      }
    }

    // Get npm package from packages
    let npmPackage: string | undefined;
    if (server.packages) {
      for (const pkg of server.packages) {
        if (pkg.registryType === 'npm') {
          npmPackage = pkg.identifier;
          break;
        } else if (pkg.registryType === 'pypi') {
          npmPackage = `pip:${pkg.identifier}`;
          break;
        }
      }
    }

    // Get config schema from environment variables
    let configSchema: Record<string, unknown> | undefined;
    if (server.packages?.[0]?.environmentVariables) {
      const envVars = server.packages[0].environmentVariables;
      configSchema = {
        type: 'object',
        properties: Object.fromEntries(
          envVars.map((v: { name: string; description?: string; isSecret?: boolean }) => [
            v.name,
            { type: 'string', description: v.description, secret: v.isSecret }
          ])
        )
      };
    }

    // Extract display name
    const nameParts = server.name.split('/');
    const displayName = server.title || nameParts[nameParts.length - 1];

    // Skip test/demo servers
    const testPatterns = [
      /\btest\b/i, /\bdemo\b/i, /\bexample\b/i, /\bsample\b/i,
      /\bhello\s*world\b/i, /\bmy\s+first\b/i, /\btodo\b/i,
      /great\s+server/i, /\balpic\b/i
    ];
    const combined = `${displayName} ${server.description || ''}`;
    if (testPatterns.some(p => p.test(combined))) {
      return null;
    }

    // Get icon from icons array
    const iconUrl = server.icons?.[0]?.src;

    const content = JSON.stringify({ name: displayName, description: server.description, tools, mcpEndpoint });
    const contentHash = crypto.createHash('sha256').update(content).digest('hex').slice(0, 16);

    return {
      id: `anthropic:${server.name}:${server.version}`,
      source: 'anthropic',
      name: server.name,
      displayName,
      description: server.description || '',
      iconUrl,
      mcpEndpoint,
      npmPackage,
      tools,
      configSchema,
      contentHash
    };
  } catch {
    return null;
  }
}

/**
 * Load registration state
 */
function loadState(): RegistrationState {
  if (fs.existsSync(STATE_FILE)) {
    return JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
  }
  return { registered: {} };
}

/**
 * Save registration state
 */
function saveState(state: RegistrationState): void {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

/**
 * Check if server needs registration
 */
function needsRegistration(server: ServerData, state: RegistrationState): { needed: boolean; reason?: string } {
  const existing = state.registered[server.id];

  if (!existing) {
    return { needed: true, reason: 'new' };
  }

  if (existing.contentHash !== server.contentHash) {
    return { needed: true, reason: 'updated' };
  }

  return { needed: false };
}

/**
 * Query subgraph to check existing registrations
 */
async function queryExistingAgents(): Promise<Set<string>> {
  const apiKey = process.env.THE_GRAPH_API_KEY;
  if (!apiKey) {
    console.warn('⚠️  THE_GRAPH_API_KEY not set, skipping subgraph check');
    return new Set();
  }

  const subgraphUrl = `https://gateway.thegraph.com/api/${apiKey}/subgraphs/id/${SUBGRAPH_ID}`;

  try {
    const response = await fetch(subgraphUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        query: `{
          agents(first: 1000) {
            id
            agentId
            registrationFile {
              name
              mcpEndpoint
            }
          }
        }`
      })
    });

    const data = await response.json() as {
      data?: {
        agents: Array<{
          id: string;
          registrationFile?: { name?: string; mcpEndpoint?: string }
        }>
      }
    };
    const agents = data.data?.agents || [];

    // Create set of existing endpoints for deduplication
    const existing = new Set<string>();
    for (const agent of agents) {
      const regFile = agent.registrationFile;
      if (regFile?.mcpEndpoint) {
        existing.add(regFile.mcpEndpoint);
      }
      if (regFile?.name) {
        existing.add(regFile.name.toLowerCase());
      }
    }

    console.log(`📊 Found ${agents.length} existing agents on-chain`);
    return existing;
  } catch (error) {
    console.warn('⚠️  Failed to query subgraph:', error);
    return new Set();
  }
}

/**
 * Register a single server
 */
async function registerServer(
  sdk: SDK,
  server: ServerData,
  dryRun: boolean
): Promise<{ agentId: string; agentURI: string } | null> {
  console.log(`\n📝 Registering: ${server.displayName}`);
  console.log(`   Source: ${server.source}`);
  console.log(`   Endpoint: ${server.mcpEndpoint || 'none'}`);
  console.log(`   Tools: ${server.tools.length}`);

  if (dryRun) {
    console.log('   [DRY RUN] Would register');
    return null;
  }

  if (!server.mcpEndpoint) {
    console.log('   ⚠️  Skipping: no MCP endpoint');
    return null;
  }

  try {
    const agent = sdk.createAgent(
      server.displayName,
      server.description || `MCP server: ${server.name}`
    );

    if (server.iconUrl) {
      agent.updateInfo(undefined, undefined, server.iconUrl);
    }

    await agent.setMCP(server.mcpEndpoint, MCP_VERSION, false);

    // Set metadata
    const regFile = agent.getRegistrationFile();
    const mcpEndpoint = regFile.endpoints.find(e => e.type === 'MCP');
    if (mcpEndpoint) {
      mcpEndpoint.meta = mcpEndpoint.meta || {};

      if (server.tools.length > 0) {
        mcpEndpoint.meta.mcpTools = server.tools;
      }
      if (server.configSchema) {
        mcpEndpoint.meta.configSchema = server.configSchema;
      }
      if (server.npmPackage) {
        mcpEndpoint.meta.npmPackage = server.npmPackage;
      }
      mcpEndpoint.meta.source = server.source;
    }

    agent.setActive(true);

    const result = await agent.registerIPFS();
    console.log(`   ✅ Registered: ${result.agentId}`);

    // Save individual registration file
    const outputFile = path.join(REGISTRATIONS_DIR, `${server.id.replace(/[/:]/g, '_')}.json`);
    fs.mkdirSync(path.dirname(outputFile), { recursive: true });
    fs.writeFileSync(outputFile, JSON.stringify({
      agentId: result.agentId,
      agentURI: result.agentURI,
      serverId: server.id,
      source: server.source,
      displayName: server.displayName,
      mcpEndpoint: server.mcpEndpoint,
      toolCount: server.tools.length,
      registeredAt: new Date().toISOString()
    }, null, 2));

    return result;
  } catch (error) {
    console.error(`   ❌ Failed:`, error);
    return null;
  }
}

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const limitArg = args.find(a => a.startsWith('--limit='));
  const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 10;
  const registryArg = args.find(a => a.startsWith('--registry='));
  const registry = registryArg?.split('=')[1] as 'smithery' | 'anthropic' | undefined;
  const toolsOnly = args.includes('--tools-only');
  const requireEndpoint = args.includes('--require-endpoint');

  console.log('🔧 ERC-8004 Batch Registration\n');
  console.log(`Limit: ${limit}`);
  console.log(`Registry: ${registry || 'all'}`);
  console.log(`Tools only: ${toolsOnly}`);
  console.log(`Require endpoint: ${requireEndpoint}`);
  console.log(`Dry run: ${dryRun}\n`);

  // Load all servers
  console.log('📂 Loading server data...');
  const servers: ServerData[] = [];

  if (!registry || registry === 'smithery') {
    const smitheryFiles = fs.readdirSync(SMITHERY_DIR).filter(f => f.endsWith('.json'));
    for (const file of smitheryFiles) {
      const server = loadSmitheryServer(path.join(SMITHERY_DIR, file));
      if (server &&
          (!toolsOnly || server.tools.length > 0) &&
          (!requireEndpoint || server.mcpEndpoint)) {
        servers.push(server);
      }
    }
    console.log(`   Smithery: ${servers.filter(s => s.source === 'smithery').length} servers`);
  }

  if (!registry || registry === 'anthropic') {
    const anthropicFiles = fs.readdirSync(ANTHROPIC_DIR).filter(f => f.endsWith('.json'));
    for (const file of anthropicFiles) {
      const server = loadAnthropicServer(path.join(ANTHROPIC_DIR, file));
      if (server &&
          (!toolsOnly || server.tools.length > 0) &&
          (!requireEndpoint || server.mcpEndpoint)) {
        servers.push(server);
      }
    }
    console.log(`   Anthropic: ${servers.filter(s => s.source === 'anthropic').length} servers`);
  }

  console.log(`   Total: ${servers.length} servers\n`);

  // Load state
  const state = loadState();
  console.log(`📊 Registration state: ${Object.keys(state.registered).length} already registered\n`);

  // Query existing on-chain registrations
  const existingOnChain = await queryExistingAgents();

  // Find servers that need registration
  const toRegister: Array<{ server: ServerData; reason: string }> = [];
  for (const server of servers) {
    const { needed, reason } = needsRegistration(server, state);

    // Also check if endpoint already exists on-chain
    if (needed && server.mcpEndpoint && existingOnChain.has(server.mcpEndpoint)) {
      continue; // Skip - already on-chain
    }
    if (needed && existingOnChain.has(server.displayName.toLowerCase())) {
      continue; // Skip - already on-chain by name
    }

    if (needed && reason) {
      toRegister.push({ server, reason });
    }
  }

  console.log(`📋 Servers to register: ${toRegister.length}`);
  if (toRegister.length === 0) {
    console.log('   Nothing to do!');
    return;
  }

  // Apply limit
  const batch = toRegister.slice(0, limit);
  console.log(`   Processing batch of ${batch.length}\n`);

  // Initialize SDK if not dry run
  let sdk: SDK | null = null;
  if (!dryRun) {
    const privateKey = process.env.PRIVATE_KEY;
    const pinataJwt = process.env.PINATA_JWT;

    if (!privateKey || !pinataJwt) {
      console.error('❌ PRIVATE_KEY and PINATA_JWT required');
      process.exit(1);
    }

    sdk = new SDK({
      chainId: CHAIN_ID,
      rpcUrl: process.env.RPC_URL || DEFAULT_RPC_URL,
      signer: privateKey,
      ipfs: 'pinata',
      pinataJwt
    });
  }

  // Register batch
  let registered = 0;
  let failed = 0;

  for (const { server, reason } of batch) {
    console.log(`\n[${registered + failed + 1}/${batch.length}] ${reason.toUpperCase()}`);

    const result = await registerServer(sdk!, server, dryRun);

    if (result) {
      state.registered[server.id] = {
        agentId: result.agentId,
        contentHash: server.contentHash,
        registeredAt: new Date().toISOString()
      };
      registered++;
    } else if (!dryRun) {
      failed++;
    }

    // Save state after each registration
    if (!dryRun) {
      saveState(state);
    }

    // Small delay to avoid rate limits
    if (!dryRun) {
      await new Promise(r => setTimeout(r, 2000));
    }
  }

  console.log('\n' + '='.repeat(50));
  console.log('Summary');
  console.log('='.repeat(50));
  console.log(`Registered: ${registered}`);
  console.log(`Failed: ${failed}`);
  console.log(`Remaining: ${toRegister.length - batch.length}`);

  if (!dryRun) {
    saveState(state);
    console.log(`\nState saved to: ${STATE_FILE}`);
  }
}

main().catch(console.error);
