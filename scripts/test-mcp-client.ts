/**
 * Test MCP Client - Call Registered MCP Server
 *
 * This script demonstrates how to use a registered MCP server by:
 * 1. Looking up the server's MCP endpoint from the registration
 * 2. Connecting via the MCP SDK with proper transport
 * 3. Calling tools via MCP protocol
 *
 * Note: Smithery-hosted servers require OAuth authentication.
 * For programmatic access, you would need to:
 * 1. Complete OAuth flow once to get tokens
 * 2. Store and refresh tokens for subsequent calls
 *
 * Usage:
 *   npx tsx scripts/test-mcp-client.ts [server-name]
 *
 * Environment:
 *   SMITHERY_API_KEY - Smithery API key for registry access
 *   EXA_API_KEY - Exa API key (passed as config to server)
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
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
 * Load source data for config schema
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
 * Get config for MCP server based on required parameters
 */
function getServerConfig(serverName: string): Record<string, string> {
  const config: Record<string, string> = {};

  // Server-specific config mapping
  if (serverName === 'exa') {
    const exaKey = process.env.EXA_API_KEY;
    if (exaKey) {
      config.exaApiKey = exaKey;
    }
  }

  return config;
}

/**
 * Build URL with config parameters
 * Smithery passes config via URL query params
 */
function buildMcpUrl(baseEndpoint: string, config: Record<string, string>): URL {
  let endpoint = baseEndpoint;
  if (!endpoint.endsWith('/mcp')) {
    endpoint = `${endpoint}/mcp`;
  }

  const url = new URL(endpoint);
  for (const [key, value] of Object.entries(config)) {
    url.searchParams.set(key, value);
  }

  return url;
}

async function main() {
  const args = process.argv.slice(2);
  const serverName = args[0] || 'exa';

  console.log('🔌 MCP Client Test (using @modelcontextprotocol/sdk)\n');
  console.log(`Server: ${serverName}`);

  // Load registration
  const registration = loadRegistration(serverName);
  if (!registration) {
    console.error(`❌ No registration found for '${serverName}'`);
    console.log('\nAvailable registrations:');
    const files = fs.readdirSync(REGISTRATIONS_DIR);
    files.forEach(f => console.log(`  - ${f.replace('.json', '')}`));
    process.exit(1);
  }

  console.log(`\n📋 Registration Data (from ERC-8004):`);
  console.log(`   Agent ID: ${registration.agentId}`);
  console.log(`   Display Name: ${registration.displayName}`);
  console.log(`   MCP Endpoint: ${registration.mcpEndpoint}`);
  console.log(`   Tool Count: ${registration.toolCount}`);

  // Load source data for additional info
  const sourceData = loadSourceData(serverName);
  if (sourceData) {
    console.log(`\n📦 Source Data:`);
    console.log(`   Description: ${(sourceData.description as string)?.slice(0, 80)}...`);
  }

  // Get server config
  const config = getServerConfig(serverName);
  if (Object.keys(config).length > 0) {
    console.log(`\n🔑 Config Parameters:`);
    for (const key of Object.keys(config)) {
      console.log(`   ${key}: ***`);
    }
  }

  // Build MCP URL with config
  const mcpUrl = buildMcpUrl(registration.mcpEndpoint, config);
  console.log(`\n🌐 MCP URL: ${mcpUrl.toString().replace(/exaApiKey=[^&]+/, 'exaApiKey=***')}`);

  // Create MCP client
  console.log('\n' + '='.repeat(50));
  console.log('Connecting to MCP Server');
  console.log('='.repeat(50));

  console.log('\n⚠️  Note: Smithery-hosted servers require OAuth authentication.');
  console.log('   For production use, you would need to implement an OAuthClientProvider');
  console.log('   that handles the authorization flow.\n');

  console.log('   Reference: https://smithery.ai/docs/use/connect');
  console.log('   The OAuth flow requires:');
  console.log('   1. User authorization via Smithery web interface');
  console.log('   2. Token storage and refresh handling');
  console.log('   3. Callback URL configuration\n');

  // Try to connect (will likely fail without OAuth, but demonstrates the pattern)
  const client = new Client({
    name: 'erc8004-mcp-test',
    version: '1.0.0'
  });

  try {
    console.log('📡 Attempting connection (without OAuth - expected to fail)...\n');

    const transport = new StreamableHTTPClientTransport(mcpUrl);
    await client.connect(transport);

    console.log('✅ Connected successfully!\n');

    // List tools
    console.log('📋 Available Tools:');
    const toolsResult = await client.listTools();
    for (const tool of toolsResult.tools) {
      console.log(`   - ${tool.name}: ${tool.description?.slice(0, 60)}...`);
    }

    // Call a tool if it's exa
    if (serverName === 'exa') {
      console.log('\n🔍 Executing web_search_exa...');
      const searchResult = await client.callTool({
        name: 'web_search_exa',
        arguments: {
          query: 'ERC-8004 agent registry blockchain',
          numResults: 3
        }
      });
      console.log('✅ Search result:');
      console.log(JSON.stringify(searchResult, null, 2).slice(0, 500) + '...');
    }

    await client.close();

  } catch (error) {
    const err = error as Error;
    console.log(`❌ Connection failed: ${err.message}`);

    if (err.message.includes('401') || err.message.includes('unauthorized') || err.message.includes('invalid_token')) {
      console.log('\n📖 This is expected for Smithery-hosted servers without OAuth.');
      console.log('\n   To use Smithery MCP servers programmatically:');
      console.log('   1. Complete OAuth flow once via web browser');
      console.log('   2. Store the access/refresh tokens');
      console.log('   3. Pass tokens via OAuthClientProvider when creating transport');
      console.log('\n   Example OAuthClientProvider interface:');
      console.log('   ```typescript');
      console.log('   interface OAuthClientProvider {');
      console.log('     get redirectUrl(): string | URL;');
      console.log('     get clientMetadata(): OAuthClientMetadata;');
      console.log('     clientInformation(): OAuthClientInformation | Promise<OAuthClientInformation>;');
      console.log('     tokens(): OAuthTokens | undefined | Promise<OAuthTokens | undefined>;');
      console.log('     saveTokens(tokens: OAuthTokens): void | Promise<void>;');
      console.log('     redirectToAuthorization(authUrl: URL): void | Promise<void>;');
      console.log('     saveCodeVerifier(codeVerifier: string): void | Promise<void>;');
      console.log('     codeVerifier(): string | Promise<string>;');
      console.log('   }');
      console.log('   ```');
    }

    console.log('\n' + '='.repeat(50));
    console.log('Alternative: Run MCP Server Locally');
    console.log('='.repeat(50));
    console.log('\n   For testing without OAuth, you can run MCP servers locally:');
    console.log('   1. npx @smithery/cli run exa --config \'{"exaApiKey":"YOUR_KEY"}\'');
    console.log('   2. This spawns a local process using stdio transport');
    console.log('   3. No OAuth required for local execution');
  }

  console.log('\n' + '='.repeat(50));
  console.log('Summary: What Was Demonstrated');
  console.log('='.repeat(50));
  console.log(`
1. ✅ Loaded registration from ERC-8004 (Agent ID: ${registration.agentId})
2. ✅ Extracted MCP endpoint: ${registration.mcpEndpoint}
3. ✅ Built connection URL with config parameters
4. ❌ OAuth authentication required for Smithery-hosted servers

The ERC-8004 registration provides:
- Agent ID for on-chain identity
- MCP endpoint URL
- Tool metadata (count: ${registration.toolCount})

To complete the flow, implement OAuth or run servers locally.
`);
}

main().catch(console.error);
