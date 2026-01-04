/**
 * Register specific Official MCP Registry server
 *
 * Usage: npx tsx scripts/register-official.ts <server-file> [--dry-run]
 */

import { SDK } from 'agent0-sdk';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const CHAIN_ID = 84532;
const MCP_VERSION = '2025-06-18';
const OFFICIAL_DIR = path.join(__dirname, '../data/sources/official/servers');
const REGISTRATIONS_DIR = path.join(__dirname, '../data/registrations');

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const serverFile = args.find(a => !a.startsWith('--'));

  if (!serverFile) {
    console.log('Usage: npx tsx scripts/register-official.ts <filename> [--dry-run]');
    console.log('\nAvailable files:');
    const files = fs.readdirSync(OFFICIAL_DIR)
      .filter(f => f.endsWith('.json'))
      .slice(0, 20);
    files.forEach(f => console.log(`  ${f}`));
    process.exit(1);
  }

  const filepath = path.join(OFFICIAL_DIR, serverFile);
  if (!fs.existsSync(filepath)) {
    console.error(`File not found: ${filepath}`);
    process.exit(1);
  }

  const data = JSON.parse(fs.readFileSync(filepath, 'utf-8'));
  const server = data.server;

  if (!server) {
    console.error('Invalid file format');
    process.exit(1);
  }

  // Extract info
  const name = server.title || server.name.split('/').pop();
  const description = server.description || '';
  const iconUrl = server.icons?.[0]?.src;
  const mcpEndpoint = server.remotes?.[0]?.url;
  const publisherMeta = server._meta?.['io.modelcontextprotocol.registry/publisher-provided'];
  const tools = (publisherMeta?.tools || []).map((t: { name: string }) => t.name);

  console.log('🔧 Register Official MCP Server\n');
  console.log(`Name: ${name}`);
  console.log(`Description: ${description.slice(0, 80)}...`);
  console.log(`Icon: ${iconUrl || 'none'}`);
  console.log(`Endpoint: ${mcpEndpoint || 'none'}`);
  console.log(`Tools: ${tools.length}`);
  console.log(`Dry run: ${dryRun}\n`);

  if (!mcpEndpoint) {
    console.error('❌ No MCP endpoint found');
    process.exit(1);
  }

  if (dryRun) {
    console.log('✅ Dry run complete');
    process.exit(0);
  }

  const privateKey = process.env.PRIVATE_KEY;
  const pinataJwt = process.env.PINATA_JWT;

  if (!privateKey || !pinataJwt) {
    console.error('❌ PRIVATE_KEY and PINATA_JWT required');
    process.exit(1);
  }

  const sdk = new SDK({
    chainId: CHAIN_ID,
    rpcUrl: process.env.RPC_URL || 'https://sepolia.base.org',
    signer: privateKey,
    ipfs: 'pinata',
    pinataJwt
  });

  const agent = sdk.createAgent(name, description);

  if (iconUrl) {
    agent.updateInfo(undefined, undefined, iconUrl);
  }

  await agent.setMCP(mcpEndpoint, MCP_VERSION, false);

  // Set metadata
  const regFile = agent.getRegistrationFile();
  const endpoint = regFile.endpoints.find(e => e.type === 'MCP');
  if (endpoint) {
    endpoint.meta = endpoint.meta || {};
    if (tools.length > 0) {
      endpoint.meta.mcpTools = tools;
    }
    endpoint.meta.source = 'official';
    endpoint.meta.version = server.version;
  }

  agent.setActive(true);

  console.log('⛓️  Registering on-chain...');
  const result = await agent.registerIPFS();

  console.log(`\n✅ Registered: ${result.agentId}`);
  console.log(`   URI: ${result.agentURI}`);

  // Save
  const safeName = server.name.replace(/[/:]/g, '_');
  const outputFile = path.join(REGISTRATIONS_DIR, `official_${safeName}.json`);
  fs.mkdirSync(REGISTRATIONS_DIR, { recursive: true });
  fs.writeFileSync(outputFile, JSON.stringify({
    agentId: result.agentId,
    agentURI: result.agentURI,
    source: 'official',
    serverName: server.name,
    displayName: name,
    mcpEndpoint,
    registeredAt: new Date().toISOString()
  }, null, 2));

  console.log(`\n📁 Saved: ${outputFile}`);
}

main().catch(console.error);
