// Run as the same nonroot UID as the daemon. Never log secret contents.
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { bundledLarkCli } from '../packages/daemon/src/lark-cli-bin.mjs';
const manifest = JSON.parse(fs.readFileSync('/config/config.json', 'utf8'));
const agent = manifest.agents.find((entry) => entry.id === manifest.defaultInstance);
if (!agent) throw new Error('Default Channel instance is missing');
for (const directory of [agent.dataDir, agent.workspace, agent.tempDir, '/data/lark-cli']) {
  if (!directory || !directory.startsWith('/data/')) throw new Error('Container state must be under /data');
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 }); fs.chmodSync(directory, 0o700);
}
if (!(agent.connectors ?? [agent.channel]).includes('lark')) {
  console.log('Private Channel directories ready; configure the selected connector identities separately');
  process.exit(0);
}
const credentials = JSON.parse(fs.readFileSync('/credentials/lark-ws.json', 'utf8'));
if (!credentials.appId || !credentials.appSecret) throw new Error('Missing Lark SDK credentials');
const target = manifest.env.VIKA_LARK_WS_CREDENTIALS;
if (!target.startsWith(`${agent.dataDir}/`)) throw new Error('SDK credentials must live in the instance data directory');
fs.writeFileSync(target, JSON.stringify(credentials), { mode: 0o600 }); fs.chmodSync(target, 0o600);
let profile;
try { profile = JSON.parse(fs.readFileSync('/data/lark-cli/config.json', 'utf8')).apps.find((app) => app.name === agent.profile); } catch {}
if (profile && profile.appId !== credentials.appId) throw new Error('Existing profile belongs to another bot; refusing to overwrite');
if (!profile) {
  const result = spawnSync(bundledLarkCli(), ['config', 'init', '--name', agent.profile, '--app-id', credentials.appId,
    '--app-secret-stdin', '--brand', 'feishu'], { input: credentials.appSecret,
    env: { ...process.env, LARKSUITE_CLI_CONFIG_DIR: '/data/lark-cli' }, encoding: 'utf8', timeout: 30000 });
  if (result.status !== 0) throw new Error('Lark CLI profile initialization failed (provider output withheld)');
}
console.log('Private Channel directories and matching Lark profile ready');
