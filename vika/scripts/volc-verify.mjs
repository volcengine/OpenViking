#!/usr/bin/env node
// Live MA verification with real VolcAgent + ActionService + SQLite, and a
// recording connector. This script NEVER connects to or sends through an IM.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { getConfig } from '../packages/daemon/src/config.mjs';
import { Store } from '../packages/daemon/src/store.mjs';
import { ActionService } from '../packages/daemon/src/actions/service.mjs';
import { VolcAgent } from '../packages/daemon/src/volc.mjs';
import { TimerRepository } from '../packages/daemon/src/timers.mjs';
import { getConnectorManifest } from '../packages/daemon/src/connectors/catalog.mjs';
import { normalizeEvent } from '../packages/daemon/src/event.mjs';
import { eventMessage } from '../packages/daemon/src/connectors/protocol.mjs';
import { formatInboundLine } from '../packages/daemon/src/runtime-input.mjs';
import { safeError } from '../packages/daemon/src/logger.mjs';
const args = process.argv.slice(2);
const largeFile = args.includes('--large-file') || args.includes('--large-output');
const largeOutput = args.includes('--large-output');
const crashDownload = args.includes('--crash-download');
const interruptDownload = args.includes('--interrupt-download') || crashDownload;
const freshness = args.includes('--freshness');
const steerReaction = args.includes('--steer-reaction');
const steering = args.includes('--steer') || args.includes('--crash-steer') || steerReaction;
if (freshness && ['--recover', '--crash', '--media', '--image-file'].some((flag) => args.includes(flag))) throw new Error('--freshness requires a fresh, separate verification run');
const directory = args[args.indexOf('--state-dir') + 1];
if (!args.includes('--state-dir') || !path.isAbsolute(directory ?? '')) throw new Error('Pass --state-dir /absolute/isolated-test-directory');
const require = createRequire(import.meta.url);
const { loadDeploymentManifest, applyDeploymentAgentEnv, applyDeploymentDaemonEnv } = require('../deployment-config.cjs');
const deployment = loadDeploymentManifest(process.env);
applyDeploymentDaemonEnv(process.env, deployment); applyDeploymentAgentEnv(process.env, deployment);
const original = getConfig();
if (directory === original.dataDir || directory.startsWith(`${original.dataDir}/`)) throw new Error('Verification state must be separate from the daemon data');
fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
const config = { ...original, dataDir: directory, workspace: path.join(directory, 'workspace'), tempDir: path.join(directory, 'tmp') };
fs.mkdirSync(config.workspace, { recursive: true, mode: 0o700 });
const logger = { info() {}, warn: (message, fields) => console.log(JSON.stringify({ message, code: fields?.code })), error() {}, debug() {} };
const store = new Store(path.join(directory, 'verification.sqlite3'), logger);
store.initializeCognition('volc', randomUUID());
if (args.includes('--crash-steer')) {
  const acceptSteer = store.acceptManagedSteer.bind(store);
  store.acceptManagedSteer = (...values) => { const result = acceptSteer(...values); process.kill(process.pid, 'SIGKILL'); return result; };
}
const effectsPath = path.join(directory, 'connector-effects.ndjson');
const imageFile = args.includes('--image-file') ? args[args.indexOf('--image-file') + 1] : null;
const source = path.join(directory, imageFile ? 'incoming.png' : 'incoming.txt');
if (imageFile) fs.copyFileSync(imageFile, source);
else if (largeFile) {
  const fd = fs.openSync(source, 'w', 0o600);
  try { const chunk = Buffer.alloc(1024 * 1024, 'V'); for (let i = 0; i < 24; i++) fs.writeSync(fd, chunk); }
  finally { fs.closeSync(fd); }
} else fs.writeFileSync(source, 'VIKA_MEDIA_SENTINEL_7f3a');
async function fileDigest(filename) {
  const hash = createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  return hash.digest('hex');
}
const downloadAttempts = [];
let downloadInterrupted = false;
const verifiedFetch = async (url, options = {}) => {
  const response = await fetch(url, options);
  if (String(url).startsWith(config.volcBaseUrl) || !largeFile) return response;
  downloadAttempts.push({ range: options.headers?.Range ?? null, status: response.status });
  if (!interruptDownload || downloadInterrupted || !response.ok) return response;
  downloadInterrupted = true;
  const reader = response.body.getReader(); let received = 0;
  const body = new ReadableStream({
    async pull(controller) {
      if (received >= 3 * 1024 * 1024) { if (crashDownload) process.kill(process.pid, 'SIGKILL'); await reader.cancel(); controller.error(new Error('Injected network interruption after 3 MiB')); return; }
      const result = await reader.read();
      if (result.done) controller.close(); else { received += result.value.byteLength; controller.enqueue(result.value); }
    }, cancel: () => reader.cancel(),
  });
  return new Response(body, { status: response.status, headers: response.headers });
};
const connectors = {
  manifests: () => config.connectors.map((id) => [id, getConnectorManifest(id)]), owner: () => null,
  invoke: async (connector, operation, input) => {
    if (operation === 'message.media') return { media: [{ kind: imageFile ? 'image' : 'file', name: path.basename(source), size: fs.statSync(source).size, mime_type: imageFile ? 'image/png' : 'text/plain', path: source }] };
    if (!['message.send', 'message.reply', 'message.react', 'message.reaction.delete'].includes(operation)) throw new Error('Verification recorder only supports message delivery and media queries');
    const file = input.content?.type === 'file' ? { bytes: fs.statSync(input.content.path).size, sha256: await fileDigest(input.content.path) } : null;
    fs.appendFileSync(effectsPath, `${JSON.stringify({ connector, operation, input, file })}\n`, { mode: 0o600 });
    if (args.includes('--crash')) process.kill(process.pid, 'SIGKILL');
    if (operation === 'message.react') return { reaction_id: `reaction_verification${Date.now()}` };
    if (operation === 'message.reaction.delete') return { deleted: true };
    return { message_id: `om_verification${Date.now()}` };
  },
};
const actions = new ActionService({ config, store, connectors, meetingEnabled: config.connectors.includes('lark'),
  timers: new TimerRepository(store.db), logger });
let complete;
let turnsCompleted = 0;
let correctionQueued = false;
function ingest(content) {
  return store.ingest(normalizeEvent(eventMessage('lark', {
    id: `verify:${randomUUID()}`, instance: 'lark', kind: 'message.created', occurred_at: Date.now(),
    subject: { type: 'conversation', id: 'oc_verify12345678' }, actor: { type: 'user', id: 'ou_verify12345678' },
    content: { text: content }, conversation: { id: 'oc_verify12345678', type: 'p2p' },
    message: { id: `om_verify${randomUUID().replaceAll('-', '')}`, type: 'text' },
  }))).inbound;
}
const coreCall = actions.call.bind(actions);
if (freshness) actions.call = async (name, input, options) => {
  // Deterministic concurrent-ingress boundary: the old real MA call has arrived,
  // but has not crossed Core's freshness gate. The correction is authenticated
  // by this isolated test harness and queued through the actual adapter.
  if (!correctionQueued && ['send_message', 'reply_message'].includes(name)) {
    correctionQueued = true;
    const newer = ingest('Correction: cancel the prior request. Send exactly VIKA_FRESH_NEW_OK to this chat. Never send the old marker.');
    if (!agent.deliver(formatInboundLine(newer), newer.seq)) throw new Error('Correction was not admitted');
  }
  return coreCall(name, input, options);
};
const completed = new Promise((resolve) => { complete = resolve; });
const timeout = setTimeout(() => { console.error('MA verification timed out; retain state for recovery'); process.exit(1); }, largeFile ? 15 * 60 * 1000 : 240000);
const agent = new VolcAgent(config, store, logger, { actions, fetch: verifiedFetch,
  onTurnSucceeded: () => { turnsCompleted += 1; if (!freshness || (correctionQueued && fs.existsSync(effectsPath))) complete({ success: true }); }, onTurnFailed: (info) => complete({ success: false, error: safeError(info.error) }) });
let steerInjected = false;
if (steering) {
  const handleEvent = agent._handleEvent.bind(agent);
  agent._handleEvent = (event) => {
    handleEvent(event);
    if (!steerInjected && event.type === 'agent.tool_use' && /sleep/.test(event.input?.command ?? '')) {
      steerInjected = true;
      setTimeout(() => {
        const next = ingest(steerReaction
          ? 'Steering update: continue the original long task. At your next scheduling boundary, first react to THIS message with OnIt and temporary=true (the authenticated idle metadata permits this working indicator), then run another Bash sleep 5, then finish by sending VIKA_STEER_OK using THIS newest event as decision_id. Do not send the old marker or a final reaction; the temporary reaction must leave room for your final result.'
          : 'Steering update while your Bash is running: replace the old marker with VIKA_STEER_OK, and send that marker to this chat at your next tool scheduling boundary. Do not send the old marker.');
        agent.deliver(formatInboundLine(next, null, steerReaction && !config.stagedReplies ? { idleForMs: 600000 } : {}), next.seq);
      }, 1000);
    }
  };
}
await agent.start();
if (!args.includes('--recover')) {
  const content = largeOutput ? 'Protocol verification with a recording connector. In cloud Bash create /mnt/session/outputs/verified-large.txt containing exactly 24 * 1024 * 1024 bytes, each byte ASCII V. Use Python with 1 MiB blocks repeated 24 times, then deliver this file with send_message file_path=verified-large.txt. Do not substitute text for the file.' : largeFile ? 'Protocol verification with a recording connector. Get this message attachment using get_message_media, use cloud Bash to copy the exact mounted incoming.txt (24 MiB) to /mnt/session/outputs/verified-large.txt, then deliver it using send_message file_path=verified-large.txt. Do not truncate, summarize, or replace the file with text. The file contains repeated ASCII V bytes.' : steering ? 'Protocol verification. Run cloud Bash sleep 15, then send VIKA_STEER_OLD to this chat with send_message. Process any incoming steering update before sending.' : freshness ? 'Protocol verification with a recording connector. Run Bash sleep 2, then send exactly VIKA_FRESH_OLD_BLOCKED to this chat with send_message.' : imageFile ? 'Protocol verification with a recording connector. Call get_message_media for this message and describe the two main colors of the returned image in a send_message to this chat. Use the image content itself, not the filename.' : args.includes('--media')
    ? 'Protocol verification with a recording connector. Get this message attachment using get_message_media, read incoming.txt in bash, copy its exact contents into /mnt/session/outputs/verified.txt and deliver that file to this chat with send_message file_path=verified.txt. Do not substitute text for the file.'
    : 'Protocol verification with a recording connector. Send exactly VIKA_CLOUD_CORE_OK to this chat with send_message.';
  const inbound = ingest(content);
  if (!agent.deliver(formatInboundLine(inbound), inbound.seq)) throw new Error('Verification input was not admitted');
}
const result = await completed; clearTimeout(timeout);
const tools = store.db.prepare('SELECT name,status,is_error,result_json FROM ma_tool_calls').all();
const effects = fs.existsSync(effectsPath) ? fs.readFileSync(effectsPath, 'utf8').trim().split('\n').filter(Boolean).length : 0;
const evidence = { result, turnsCompleted, correctionQueued, downloadAttempts, downloadInterrupted, sessionId: agent.sessionId, effects, tools, outbox: store.db.prepare('SELECT status FROM outbox').all() };
fs.writeFileSync(path.join(directory, 'evidence.json'), JSON.stringify(evidence, null, 2), { mode: 0o600 });
console.log(JSON.stringify({ ...result, sessionId: agent.sessionId, effects, tools: tools.map(({ name, status, is_error }) => ({ name, status, is_error })) }));
await agent.stop(); store.close();
if (freshness) {
  assert.equal(correctionQueued, true);
  assert.ok(turnsCompleted >= 1 && turnsCompleted <= 2);
  assert.equal(effects, 1);
  assert.ok(tools.some((tool) => tool.is_error && tool.result_json.includes('stale_decision')), 'Old real MA delivery must hit Core stale_decision');
  const recorded = fs.readFileSync(effectsPath, 'utf8');
  assert.match(recorded, /VIKA_FRESH_NEW_OK/);
  assert.doesNotMatch(recorded, /VIKA_FRESH_OLD_BLOCKED/);
}

if (steering) {
  assert.equal(steerInjected, true);
  assert.equal(turnsCompleted, 1, 'Steering must finish in the same logical run');
  if (!steerReaction) assert.equal(effects, 1);
  const recorded = fs.readFileSync(effectsPath, 'utf8');
  if (steerReaction) {
    const operations = recorded.trim().split('\n').map((line) => JSON.parse(line).operation);
    assert.equal(operations.filter((operation) => ['message.send', 'message.reply'].includes(operation)).length, 1);
    assert.ok(operations.includes('message.react'));
    assert.equal(operations.at(-1), 'message.reaction.delete');
  }
  assert.match(recorded, /VIKA_STEER_OK/);
  assert.doesNotMatch(recorded, /VIKA_STEER_OLD/);
}
if (largeFile) {
  const records = fs.readFileSync(effectsPath, 'utf8').trim().split('\n').map(JSON.parse);
  const delivered = records.filter((record) => record.file);
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].file.bytes, 24 * 1024 * 1024);
  assert.equal(delivered[0].file.sha256, await fileDigest(source));
  if (interruptDownload) assert.ok(downloadAttempts.some((attempt) => attempt.range && attempt.status === 206), 'Real storage must resume with HTTP 206');
}
if (!result.success || (args.includes('--recover') && effects !== 1)) process.exitCode = 1;
