import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SMITHERY_DIR = path.join(__dirname, '../data/sources/smithery/servers');
const OFFICIAL_DIR = path.join(__dirname, '../data/sources/official/servers');

const testPatterns = [
  /\btest\b/i, /\bdemo\b/i, /\bexample\b/i, /\bsample\b/i,
  /\bhello\s*world\b/i, /\bmy\s+first\b/i, /\btodo\b/i,
  /great\s+server/i, /\balpic\b/i
];

function isTestServer(name, description) {
  const combined = (name || '') + ' ' + (description || '');
  return testPatterns.some(p => p.test(combined));
}

// Smithery
console.log('=== SMITHERY REGISTRY ===\n');
const smitheryFiles = fs.readdirSync(SMITHERY_DIR).filter(f => f.endsWith('.json'));
let sTotal = 0, sEndpoint = 0, sTools = 0, sBoth = 0, sProd = 0, sProdBoth = 0;

for (const file of smitheryFiles) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(SMITHERY_DIR, file), 'utf-8'));
    sTotal++;
    const hasE = Boolean(data.deploymentUrl);
    const hasT = data.tools && data.tools.length > 0;
    const isTest = isTestServer(data.displayName, data.description);
    if (hasE) sEndpoint++;
    if (hasT) sTools++;
    if (hasE && hasT) sBoth++;
    if (!isTest) { sProd++; if (hasE && hasT) sProdBoth++; }
  } catch (e) {}
}
console.log('Total:              ' + sTotal);
console.log('With endpoint:      ' + sEndpoint);
console.log('With tools:         ' + sTools);
console.log('Endpoint + tools:   ' + sBoth);
console.log('After test filter:  ' + sProd);
console.log('Production + both:  ' + sProdBoth);

// Official
console.log('\n=== OFFICIAL MCP REGISTRY ===\n');
const officialFiles = fs.readdirSync(OFFICIAL_DIR).filter(f => f.endsWith('.json'));
let oTotal = 0, oEndpoint = 0, oTools = 0, oBoth = 0, oProd = 0, oProdBoth = 0, oRepo = 0, oNpm = 0;

for (const file of officialFiles) {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(OFFICIAL_DIR, file), 'utf-8'));
    const server = raw.server;
    if (!server) continue;
    oTotal++;
    const hasE = server.remotes && server.remotes.length > 0;
    // Tools can be at server.tools OR nested in packages[].tools
    let toolCount = 0;
    if (server.tools && server.tools.length > 0) {
      toolCount = server.tools.length;
    } else if (server.packages) {
      for (const pkg of server.packages) {
        if (pkg.tools && pkg.tools.length > 0) {
          toolCount += pkg.tools.length;
        }
      }
    }
    const hasT = toolCount > 0;
    const displayName = server.title || server.name;
    const isTest = isTestServer(displayName, server.description);
    const hasRepo = server.repository && server.repository.url;
    const hasNpm = server.packages && server.packages.npm;
    if (hasE) oEndpoint++;
    if (hasT) oTools++;
    if (hasE && hasT) oBoth++;
    if (hasRepo) oRepo++;
    if (hasNpm) oNpm++;
    if (!isTest) { oProd++; if (hasE && hasT) oProdBoth++; }
  } catch (e) {}
}
console.log('Total:              ' + oTotal);
console.log('With endpoint:      ' + oEndpoint);
console.log('With tools:         ' + oTools);
console.log('Endpoint + tools:   ' + oBoth);
console.log('With repository:    ' + oRepo);
console.log('With npm package:   ' + oNpm);
console.log('After test filter:  ' + oProd);
console.log('Production + both:  ' + oProdBoth);

console.log('\n=== COMBINED SUMMARY ===\n');
console.log('Total servers:           ' + (sTotal + oTotal));
console.log('With endpoint:           ' + (sEndpoint + oEndpoint));
console.log('With tools:              ' + (sTools + oTools));
console.log('Production-ready:        ' + (sProdBoth + oProdBoth) + ' (endpoint + tools, not test)');
