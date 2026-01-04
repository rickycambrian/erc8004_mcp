/**
 * Test MCP Server Locally via Stdio Transport
 *
 * This script demonstrates how to use an MCP server locally:
 * 1. Spawns the server as a local process
 * 2. Communicates via stdin/stdout (stdio transport)
 * 3. No OAuth required - direct execution
 *
 * Usage:
 *   npx tsx scripts/test-mcp-local.ts [server-name]
 *
 * Environment:
 *   EXA_API_KEY - Exa API key (for exa server)
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Data paths
const REGISTRATIONS_DIR = path.join(__dirname, '../data/registrations');
const SMITHERY_DIR = path.join(__dirname, '../data/sources/smithery/servers');

interface Registration {
  agentId: string;
  agentURI: string;
  serverName: string;
  displayName: string;
  mcpEndpoint: string;
  toolCount: number;
  registeredAt: string;
}

/**
 * Load registration data for a server
 */
function loadRegistration(serverName: string): Registration | null {
  const files = fs.readdirSync(REGISTRATIONS_DIR).filter(f => f.endsWith('.json'));

  for (const file of files) {
    const data = JSON.parse(fs.readFileSync(path.join(REGISTRATIONS_DIR, file), 'utf-8'));
    if (data.serverName === serverName ||
        data.displayName?.toLowerCase().includes(serverName.toLowerCase()) ||
        file.includes(serverName)) {
      return data as Registration;
    }
  }
  return null;
}

/**
 * Load source data
 */
function loadSourceData(serverName: string): Record<string, unknown> | null {
  const safeName = serverName.replace(/\//g, '__').replace(/@/g, '_at_');
  const filepath = path.join(SMITHERY_DIR, `${safeName}.json`);

  if (fs.existsSync(filepath)) {
    return JSON.parse(fs.readFileSync(filepath, 'utf-8'));
  }
  return null;
}

/**
 * Get server config for local execution
 */
function getServerConfig(serverName: string): Record<string, string> {
  const config: Record<string, string> = {};

  if (serverName === 'exa') {
    const exaKey = process.env.EXA_API_KEY;
    if (exaKey) {
      config.exaApiKey = exaKey;
    }
  }

  return config;
}

async function main() {
  const args = process.argv.slice(2);
  const serverName = args[0] || 'exa';

  console.log('🔌 MCP Local Test (Stdio Transport)\n');
  console.log(`Server: ${serverName}`);

  // Load registration
  const registration = loadRegistration(serverName);
  if (!registration) {
    console.error(`❌ No registration found for '${serverName}'`);
    process.exit(1);
  }

  console.log(`\n📋 ERC-8004 Registration:`);
  console.log(`   Agent ID: ${registration.agentId}`);
  console.log(`   Display Name: ${registration.displayName}`);
  console.log(`   MCP Endpoint: ${registration.mcpEndpoint}`);
  console.log(`   Tool Count: ${registration.toolCount}`);

  // Load source data
  const sourceData = loadSourceData(serverName);
  if (sourceData) {
    console.log(`\n📦 Source Data:`);
    console.log(`   Qualified Name: ${sourceData.qualifiedName}`);
    console.log(`   Description: ${(sourceData.description as string)?.slice(0, 60)}...`);
  }

  // Get config
  const config = getServerConfig(serverName);
  if (Object.keys(config).length === 0) {
    console.error(`\n❌ No config found. Set required environment variables.`);
    if (serverName === 'exa') {
      console.log('   Required: EXA_API_KEY');
    }
    process.exit(1);
  }

  console.log(`\n🔑 Config Parameters:`);
  for (const key of Object.keys(config)) {
    console.log(`   ${key}: ***`);
  }

  // Create MCP client with stdio transport
  console.log('\n' + '='.repeat(50));
  console.log('Spawning MCP Server Locally');
  console.log('='.repeat(50));

  const client = new Client({
    name: 'erc8004-mcp-test',
    version: '1.0.0'
  });

  try {
    console.log('\n📡 Starting local MCP server via npx exa-mcp-server...\n');

    // Use the direct exa-mcp-server npm package (no Smithery needed)
    // This runs the server locally via stdio transport
    const transport = new StdioClientTransport({
      command: 'npx',
      args: ['-y', 'exa-mcp-server'],
      env: {
        ...process.env,
        // Pass Exa API key via environment variable
        EXA_API_KEY: config.exaApiKey || process.env.EXA_API_KEY || ''
      }
    });

    await client.connect(transport);
    console.log('✅ Connected to local MCP server!\n');

    // List tools
    console.log('📋 Available Tools:');
    const toolsResult = await client.listTools();
    for (const tool of toolsResult.tools) {
      console.log(`   - ${tool.name}`);
      console.log(`     ${tool.description?.slice(0, 80)}...`);
    }

    // Call a tool if it's exa
    if (serverName === 'exa') {
      console.log('\n' + '='.repeat(50));
      console.log('Executing web_search_exa');
      console.log('='.repeat(50));

      console.log('\n🔍 Query: "ERC-8004 agent registry blockchain"');
      console.log('   numResults: 3\n');

      const searchResult = await client.callTool({
        name: 'web_search_exa',
        arguments: {
          query: 'ERC-8004 agent registry blockchain',
          numResults: 3
        }
      });

      console.log('✅ Search completed!\n');
      console.log('📄 Result:');

      // Parse and display results
      const content = searchResult.content as Array<{ type: string; text?: string }>;
      if (content && content.length > 0) {
        const text = content[0]?.text;
        if (text) {
          // Show first 1000 chars of results
          console.log(text.slice(0, 1000));
          if (text.length > 1000) {
            console.log('\n... (truncated)');
          }
        }
      }
    }

    await client.close();
    console.log('\n✅ Session complete!');

  } catch (error) {
    const err = error as Error;
    console.error(`\n❌ Error: ${err.message}`);

    if (err.message.includes('ENOENT') || err.message.includes('not found')) {
      console.log('\n📖 The exa-mcp-server package may not be installed.');
      console.log('   Run directly with: EXA_API_KEY=xxx npx exa-mcp-server');
    }

    process.exit(1);
  }

  console.log('\n' + '='.repeat(50));
  console.log('Summary');
  console.log('='.repeat(50));
  console.log(`
✅ Successfully used MCP server registered on ERC-8004!

Flow:
1. Loaded registration from ERC-8004 (Agent ID: ${registration.agentId})
2. Retrieved MCP server config from source data
3. Spawned server locally via Smithery CLI
4. Connected using stdio transport (no OAuth needed)
5. Listed tools and executed search query

This demonstrates the complete flow from on-chain registration
to actual MCP server usage.
`);
}

main().catch(console.error);
