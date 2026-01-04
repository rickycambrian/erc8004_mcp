/**
 * Register MCP Server as ERC-8004 Agent
 *
 * This script registers an MCP server from our collected data onto the
 * ERC-8004 IdentityRegistry using the agent0-sdk.
 *
 * Usage:
 *   npx tsx scripts/register-mcp.ts [server-name] [--dry-run]
 *
 * Environment:
 *   PRIVATE_KEY - Wallet private key for signing transactions
 *   RPC_URL - Base Sepolia RPC URL
 *   PINATA_JWT - Pinata JWT for IPFS pinning
 */

import { SDK } from 'agent0-sdk';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const CHAIN_ID = 84532; // Base Sepolia
const DEFAULT_RPC_URL = 'https://sepolia.base.org';
const MCP_VERSION = '2025-06-18';

// Data paths
const SMITHERY_DIR = path.join(__dirname, '../data/sources/smithery/servers');

interface SmitheryServer {
  qualifiedName: string;
  displayName: string;
  description: string;
  iconUrl?: string;
  deploymentUrl?: string;
  remote?: boolean;
  tools?: Array<{ name: string; description?: string }>;
  connections?: Array<{ type: string; deploymentUrl?: string }>;
}

/**
 * Load MCP server data from Smithery collection
 */
function loadServerData(serverName: string): SmitheryServer | null {
  // Try exact match first
  const safeName = serverName.replace(/\//g, '__').replace(/@/g, '_at_');
  const filepath = path.join(SMITHERY_DIR, `${safeName}.json`);

  if (fs.existsSync(filepath)) {
    const data = JSON.parse(fs.readFileSync(filepath, 'utf-8'));
    return data as SmitheryServer;
  }

  // Try finding by qualified name
  const files = fs.readdirSync(SMITHERY_DIR).filter(f => f.endsWith('.json'));
  for (const file of files) {
    const data = JSON.parse(fs.readFileSync(path.join(SMITHERY_DIR, file), 'utf-8'));
    if (data.qualifiedName === serverName || data.displayName?.toLowerCase() === serverName.toLowerCase()) {
      return data as SmitheryServer;
    }
  }

  return null;
}

/**
 * Get MCP endpoint URL from server data
 */
function getMcpEndpoint(server: SmitheryServer): string | null {
  // Check deploymentUrl directly
  if (server.deploymentUrl) {
    return server.deploymentUrl;
  }

  // Check connections array
  if (server.connections) {
    for (const conn of server.connections) {
      if (conn.type === 'http' && conn.deploymentUrl) {
        return conn.deploymentUrl;
      }
    }
  }

  // Construct Smithery hosted URL
  if (server.remote && server.qualifiedName) {
    return `https://server.smithery.ai/${server.qualifiedName}`;
  }

  return null;
}

/**
 * Extract tool names from server data
 */
function getToolNames(server: SmitheryServer): string[] {
  if (!server.tools || !Array.isArray(server.tools)) {
    return [];
  }
  return server.tools.map(t => t.name).filter(Boolean);
}

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const serverName = args.find(a => !a.startsWith('--')) || 'exa';

  console.log('🔧 ERC-8004 MCP Server Registration\n');
  console.log(`Server: ${serverName}`);
  console.log(`Chain: Base Sepolia (${CHAIN_ID})`);
  console.log(`Dry run: ${dryRun}\n`);

  // Load server data
  const server = loadServerData(serverName);
  if (!server) {
    console.error(`❌ Server '${serverName}' not found in collected data`);
    console.log('\nAvailable servers (sample):');
    const files = fs.readdirSync(SMITHERY_DIR).slice(0, 10);
    files.forEach(f => console.log(`  - ${f.replace('.json', '')}`));
    process.exit(1);
  }

  console.log('📦 Server Data:');
  console.log(`  Name: ${server.displayName}`);
  console.log(`  Description: ${server.description?.slice(0, 100)}...`);
  console.log(`  Icon: ${server.iconUrl || 'none'}`);

  const mcpEndpoint = getMcpEndpoint(server);
  if (!mcpEndpoint) {
    console.error('❌ No MCP endpoint found for this server');
    process.exit(1);
  }
  console.log(`  MCP Endpoint: ${mcpEndpoint}`);

  const toolNames = getToolNames(server);
  console.log(`  Tools (${toolNames.length}): ${toolNames.slice(0, 5).join(', ')}${toolNames.length > 5 ? '...' : ''}`);

  if (dryRun) {
    console.log('\n✅ Dry run complete. Registration file would contain:');
    console.log(JSON.stringify({
      name: server.displayName,
      description: server.description,
      image: server.iconUrl,
      endpoints: [{
        name: 'MCP',
        endpoint: mcpEndpoint,
        version: MCP_VERSION,
        mcpTools: toolNames
      }],
      active: true
    }, null, 2));
    process.exit(0);
  }

  // Validate environment
  const privateKey = process.env.PRIVATE_KEY;
  const rpcUrl = process.env.RPC_URL || DEFAULT_RPC_URL;
  const pinataJwt = process.env.PINATA_JWT;

  if (!privateKey) {
    console.error('❌ PRIVATE_KEY environment variable required');
    process.exit(1);
  }

  if (!pinataJwt) {
    console.error('❌ PINATA_JWT environment variable required');
    process.exit(1);
  }

  console.log('\n🚀 Initializing SDK...');

  // Initialize SDK
  const sdk = new SDK({
    chainId: CHAIN_ID,
    rpcUrl: rpcUrl,
    signer: privateKey,
    ipfs: 'pinata',
    pinataJwt: pinataJwt,
  });

  console.log('✅ SDK initialized');

  // Create agent
  console.log('\n📝 Creating agent...');
  const agent = sdk.createAgent(
    server.displayName,
    server.description || `MCP server: ${server.qualifiedName}`
  );

  // Set image if available
  if (server.iconUrl) {
    agent.updateInfo(undefined, undefined, server.iconUrl);
  }

  // Set MCP endpoint with autoFetch=false (we have the tools already)
  console.log('🔗 Setting MCP endpoint...');
  await agent.setMCP(mcpEndpoint, MCP_VERSION, false);

  // Manually set tool names from our collected data
  const regFile = agent.getRegistrationFile();
  const mcpEndpointObj = regFile.endpoints.find(e => e.type === 'MCP');
  if (mcpEndpointObj && toolNames.length > 0) {
    mcpEndpointObj.meta = mcpEndpointObj.meta || {};
    mcpEndpointObj.meta.mcpTools = toolNames;
    console.log(`  Set ${toolNames.length} tools from collected data`);
  }

  // Set as active
  agent.setActive(true);

  // Register on-chain
  console.log('\n⛓️  Registering on-chain...');
  console.log('  1. Uploading to IPFS...');
  console.log('  2. Minting agent NFT...');
  console.log('  3. Setting agent URI...');

  try {
    const result = await agent.registerIPFS();

    console.log('\n✅ Registration complete!');
    console.log(`  Agent ID: ${result.agentId}`);
    console.log(`  Agent URI: ${result.agentURI}`);
    console.log(`  Chain: Base Sepolia (${CHAIN_ID})`);

    // Save result
    const outputFile = path.join(__dirname, '../data/registrations', `${server.qualifiedName.replace(/\//g, '_')}.json`);
    fs.mkdirSync(path.dirname(outputFile), { recursive: true });
    fs.writeFileSync(outputFile, JSON.stringify({
      agentId: result.agentId,
      agentURI: result.agentURI,
      serverName: server.qualifiedName,
      displayName: server.displayName,
      mcpEndpoint,
      toolCount: toolNames.length,
      registeredAt: new Date().toISOString()
    }, null, 2));
    console.log(`\n📁 Registration saved to: ${outputFile}`);

    console.log('\n🔍 View on subgraph:');
    console.log(`  https://thegraph.com/studio/subgraph/agent0-base-sepolia/`);
    console.log(`  Query: { agent(id: "${result.agentId}") { name, mcpEndpoint, mcpTools } }`);

  } catch (error) {
    console.error('\n❌ Registration failed:', error);
    process.exit(1);
  }
}

main().catch(console.error);
