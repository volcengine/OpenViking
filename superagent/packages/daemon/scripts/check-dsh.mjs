// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import {
  DSH_BRIDGE_CLI_METHODS, DSH_BRIDGE_ID, DSH_BRIDGE_PATH, DSH_BRIDGE_VERSION, DSH_NOT_READY_CODE,
  assertDshNodeVersion, assertSupportedDshVersion, bridgeHelloSupports, dshCommand, isDshNotReady,
  retryDshNotReady, sanitizeDshEnv, writeDshPatch,
} from '../src/dsh-runtime.mjs';
import { DshBridgeFramer } from '../src/dsh-bridge/protocol.mjs';
import { terminateProcessTree } from '../src/process-utils.mjs';

// Keyless protocol smoke. The endpoint accepts TCP requests but never answers,
// so a steer is guaranteed to remain live until the bridge cancels it, and no
// assertion needs a real provider key or network.
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-check-'));
fs.chmodSync(dataDir, 0o700);
const workspace = path.join(dataDir, 'workspace');
fs.mkdirSync(workspace);
// The two documents the DSH services own; ov-superagent neither writes nor parses the
// YAML, so the check asserts presence and content, never a second read path.
const profilePatch = path.join(dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
const credentialsFile = path.join(dataDir, 'dsh', 'home', '.credentials.yaml');
const CREDENTIAL_REF = 'CHECK_ROUTE_KEY';
const CREDENTIAL_VALUE = 'check-route-secret';
const bin = process.env.DSH_BIN || 'dsh';
let child;
let stderr = '';
let server;
// The local `dsh` is a launcher for the source tree (tsx + the TypeScript CLI),
// so startup is far slower than a packaged install; the bound is generous on
// purpose. Assertions are not weakened to fit it.
const STARTUP_MS = Number(process.env.DSH_CHECK_STARTUP_MS || 120_000);
const timer = setTimeout(() => finish(new Error(`dsh bridge check timed out after ${STARTUP_MS / 1000}s`)), STARTUP_MS);

function finish(error = null) {
  clearTimeout(timer);
  if (child) terminateProcessTree(child, 'SIGKILL');
  server?.close();
  if (server) for (const socket of sockets) socket.destroy();
  fs.rmSync(dataDir, { recursive: true, force: true });
  if (error) {
    process.stderr.write(`${error.message}\n${stderr.slice(-4000)}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write('DSH bridge v2: hello, settings, credentials, models, select, initialize, steer (incl. U+2028/U+2029), cancel and shutdown passed.\n');
  }
}

const sockets = new Set();
server = http.createServer((_request, _response) => { /* Deliberately never respond. */ });
server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
const origin = `http://127.0.0.1:${server.address().port}`;
const baseURL = `${origin}/anthropic`;
// A route declared through the settings service. The endpoint is the same
// unresponsive server: listing a declared route is catalog-driven, so it must
// answer without touching the endpoint.
const routeProfile = {
  api: 'openai-completions',
  baseURL: `${origin}/v1`,
  apiKeyEnv: CREDENTIAL_REF,
  displayName: 'Check Route',
  models: [{
    id: 'deepseek-chat', name: 'DeepSeek Chat', input: ['text', 'image'],
    reasoningEfforts: { low: 'low', high: 'high', max: 'max' },
    contextWindow: 1_000_000, maxTokens: 384_000,
  }],
};

try {
  assertDshNodeVersion();
  const version = await new Promise((resolve, reject) => {
    const probe = spawn(bin, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    probe.stdout.on('data', chunk => { output += chunk; });
    probe.once('error', reject);
    probe.once('exit', code => code === 0 ? resolve(output) : reject(new Error(`${bin} --version failed (${code})`)));
  });
  assertSupportedDshVersion(version);
  // The run patch names no provider: the settings service refuses to write a
  // namespace an overlay already defines, so the check owns that document
  // through the bridge, exactly as the product does.
  const patchPath = writeDshPatch({
    dataDir, policy: 'keyless bridge smoke', bridgePath: DSH_BRIDGE_PATH, mcp: null,
  });
  const command = dshCommand({ bin, patchPath });
  const spawnedAt = Date.now();
  child = spawn(command.command, command.args, {
    cwd: workspace,
    env: sanitizeDshEnv({ ...process.env, DEEPSEEK_API_KEY: 'keyless-bridge-smoke', DEEPSEEK_BASE_URL: baseURL }, { dataDir }),
    shell: false, detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'],
  });
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => { stderr = `${stderr}${chunk}`.slice(-12000); });
  child.once('error', finish);
  child.once('exit', (code, signal) => {
    if (code !== 0) finish(new Error(`dsh exited early (code ${code}, signal ${signal})`));
  });
  child.stdin.on('error', error => finish(error));
  let nextId = 0;
  const pending = new Map();
  const events = [];
  // The child's stdout is framed the same way the bridge frames stdin: one
  // frame per LF, decoded per line. readline would split a reply whose strings
  // contain U+2028/U+2029 and make this smoke mask the bug it checks for.
  const framer = new DshBridgeFramer({
    onFrame({ text }) {
      let message;
      try { message = JSON.parse(text); } catch { return; }
      if (message.id !== undefined && pending.has(message.id)) {
        const entry = pending.get(message.id);
        pending.delete(message.id);
        // Errors are data here, not failures: the cold-start assertion reads
        // the NOT_READY frame, and rpc() is the throwing wrapper.
        entry.resolve(message.error ? { error: message.error } : { result: message.result });
      } else if (message.method) events.push(message);
    },
    onOversized(prefix) {
      stderr = `${stderr}\n[check-dsh] dropped an oversized reply frame: ${prefix}`.slice(-12000);
    },
  });
  child.stdout.on('data', chunk => framer.push(chunk));
  child.stdout.once('end', () => framer.end());
  function request(method, params = {}) {
    const id = `check-${++nextId}`;
    return new Promise((resolve) => {
      pending.set(id, { resolve, method });
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }
  async function rpc(method, params = {}) {
    const reply = await request(method, params);
    if (reply.error) throw new Error(`${method}: ${reply.error.message}`);
    return reply.result;
  }
  // The first frame races the boot: the credentials service mounts late, so
  // this is the only reliable window on the documented NOT_READY answer. The
  // initialize below is what gives the service time to activate before the
  // retry runs, keeping the retry assertion deterministic.
  const coldAttempt = request('superagent/credentials/set', { ref: 'CHECK_COLD_REF', value: 'check-cold' });
  await rpc('initialize', { cwd: workspace, provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high', approvalRequests: true });
  const startupMs = Date.now() - spawnedAt;
  const coldReply = await coldAttempt;
  if (coldReply.error) {
    assert.equal(coldReply.error.code, DSH_NOT_READY_CODE,
      `the first frame must answer NOT_READY or succeed, got ${JSON.stringify(coldReply.error)}`);
    assert.deepEqual(coldReply.error.data, { code: 'NOT_READY', service: 'credentials' });
    assert.ok(isDshNotReady(Object.assign(new Error(coldReply.error.message), { code: coldReply.error.code, data: coldReply.error.data })));
    // The same retry helper the daemon and the CLI use, with extra headroom for
    // this machine's slower source-tree launcher.
    assert.deepEqual(
      await retryDshNotReady(() => rpc('superagent/credentials/set', { ref: 'CHECK_COLD_REF', value: 'check-cold' }), { ceilingMs: 15_000 }),
      { ok: true },
    );
    process.stdout.write('DSH credentials NOT_READY was answered and retried successfully.\n');
  } else {
    process.stdout.write('DSH credentials service was already mounted for the first frame; NOT_READY was not observable.\n');
  }

  const hello = await rpc('superagent/hello');
  assert.ok(bridgeHelloSupports(hello, DSH_BRIDGE_CLI_METHODS),
    `superagent/hello did not expose bridge v${DSH_BRIDGE_VERSION}: ${JSON.stringify(hello)}`);
  assert.equal(hello.bridge, DSH_BRIDGE_ID);
  assert.equal(hello.version, DSH_BRIDGE_VERSION);
  assert.deepEqual([...hello.methods].sort(), [...DSH_BRIDGE_CLI_METHODS].sort(),
    'hello must advertise exactly the methods this release routes');

  const describedAtStart = await rpc('superagent/settings/describe', { ns: 'llm-pi-ai' });
  assert.equal(describedAtStart.ns, 'llm-pi-ai');
  assert.ok(describedAtStart.descriptor, 'llm-pi-ai must be a configurable namespace');
  assert.equal(typeof describedAtStart.descriptor.revision, 'number');
  assert.equal(typeof describedAtStart.descriptor.value.providers, 'object');
  await assert.rejects(
    () => rpc('superagent/settings/describe', { ns: 'llm' }),
    /unsupported settings namespace/,
  );

  const keylessModels = await rpc('superagent/llm/models');
  const builtIn = (provider) => keylessModels.providers.find(entry => entry.provider === provider);
  // The dummy DEEPSEEK_API_KEY admits the official route's built-in catalog;
  // the account route is never signed in here, so it stays empty and is what
  // the daemon's "models.length > 0" filter drops from /model.
  assert.ok(builtIn('deepseek-official'), `deepseek-official must be listed: ${JSON.stringify(keylessModels)}`);
  assert.ok(builtIn('deepseek-official').models.length >= 1, 'the keyed official route lists its built-in models');
  assert.deepEqual(builtIn('deepseek-account').models, [], 'the account route has no models without a sign-in');

  await rpc('superagent/settings/mutate', { ns: 'llm-pi-ai', ops: [{ op: 'set', path: ['providers', 'check-route'], value: routeProfile }] });
  const described = await rpc('superagent/settings/describe', { ns: 'llm-pi-ai' });
  assert.equal(described.descriptor.revision, describedAtStart.descriptor.revision + 1);
  assert.equal(described.descriptor.value.providers['check-route'].apiKeyEnv, CREDENTIAL_REF);
  assert.ok(fs.existsSync(profilePatch), `the settings service must write ${profilePatch}`);
  const profilePatchText = fs.readFileSync(profilePatch, 'utf8');
  assert.match(profilePatchText, /llm-pi-ai/);
  assert.match(profilePatchText, /check-route/);
  assert.doesNotMatch(fs.readFileSync(patchPath, 'utf8'), /llm-pi-ai/, 'the run patch must not overlay llm-pi-ai');

  const uncredentialed = (await rpc('superagent/llm/models')).providers.find(entry => entry.provider === 'check-route');
  assert.ok(uncredentialed, 'the new route must appear in the catalogue');
  assert.deepEqual(uncredentialed.credential, { ref: CREDENTIAL_REF, present: false, known: true });
  assert.deepEqual(uncredentialed.models, [], 'a route without its credential lists no models');

  const routeInfo = await rpc('superagent/llm/model/info', { provider: 'check-route', model: 'deepseek-chat' });
  assert.deepEqual(routeInfo.reasoning.efforts.map(effort => effort.id), ['low', 'high', 'max']);
  assert.deepEqual(routeInfo.inputModalities, ['text', 'image']);

  const sessionId = randomUUID();
  await rpc('session/working-directory/get', { sessionId });
  assert.deepEqual(
    await rpc('superagent/session/model/select', { sessionId, provider: 'check-route', model: 'deepseek-chat', reasoningEffort: 'max' }),
    { provider: 'check-route', model: 'deepseek-chat', reasoningEffort: 'max' },
  );
  // Omitting the effort records null: the adapter default is not a selection.
  assert.deepEqual(
    await rpc('superagent/session/model/select', { sessionId, provider: 'check-route', model: 'deepseek-chat' }),
    { provider: 'check-route', model: 'deepseek-chat', reasoningEffort: null },
  );
  const unsupported = await request('superagent/session/model/select', {
    sessionId, provider: 'check-route', model: 'deepseek-chat', reasoningEffort: 'medium',
  });
  assert.equal(unsupported.error?.code, -32603, JSON.stringify(unsupported.error));
  assert.equal(unsupported.error?.data?.code, 'UNSUPPORTED_REASONING_EFFORT', JSON.stringify(unsupported.error));
  assert.match(unsupported.error.message, /does not support reasoning effort "medium"/);

  await rpc('superagent/credentials/set', { ref: CREDENTIAL_REF, value: CREDENTIAL_VALUE });
  const configured = (await rpc('superagent/llm/models')).providers.find(entry => entry.provider === 'check-route');
  assert.deepEqual(configured.credential, { ref: CREDENTIAL_REF, present: true, known: true });
  assert.deepEqual(configured.models.map(model => model.id), ['deepseek-chat']);
  assert.ok(fs.existsSync(credentialsFile), `credentials/set must write ${credentialsFile}`);
  assert.equal(fs.statSync(credentialsFile).mode & 0o777, 0o600);
  assert.match(fs.readFileSync(credentialsFile, 'utf8'), new RegExp(`^\\s+${CREDENTIAL_REF}: `, 'm'));
  assert.doesNotMatch(fs.readFileSync(profilePatch, 'utf8'), new RegExp(CREDENTIAL_VALUE),
    'the profile patch references the credential, never its value');
  await assert.rejects(
    () => rpc('superagent/credentials/set', { ref: 'not a ref', value: CREDENTIAL_VALUE }),
    /POSIX shell identifier/,
  );

  const first = await rpc('session/steer', { sessionId, contentBlocks: [{ type: 'text', text: 'Wait for an answer from the provider.' }] });
  const second = await rpc('session/steer', { sessionId, contentBlocks: [{ type: 'text', text: 'This second input is deliberately queued.' }] });
  assert.ok(first.messageId && second.messageId);
  // S0-1 regression: U+2028 and U+2029 are JSON string content, not line
  // separators. One LF-framed request must be answered; readline would have
  // split it into three unparsable pieces and lost the steer silently.
  const separator = '\u2028';
  const paragraph = '\u2029';
  const unicodeText = `before${separator}middle${paragraph}after`;
  const unicode = await rpc('session/steer', { sessionId, contentBlocks: [{ type: 'text', text: unicodeText }] });
  assert.ok(unicode?.messageId, `a steer containing U+2028/U+2029 must be accepted, got ${JSON.stringify(unicode)}`);
  assert.deepEqual(await rpc('session/cancel', { sessionId, keepInbox: false }), { cancelled: true });
  await rpc('session/wait', { sessionId });
  assert.ok(events.some(event => event.method === 'session.event'
    && event.params?.sessionId === sessionId && event.params.event?.type === 'turn/end'
    && event.params.event?.data?.reason?.kind === 'aborted'), 'expected a root turn/end aborted event');

  await rpc('superagent/credentials/unset', { ref: CREDENTIAL_REF });
  const unconfigured = (await rpc('superagent/llm/models')).providers.find(entry => entry.provider === 'check-route');
  assert.deepEqual(unconfigured.credential, { ref: CREDENTIAL_REF, present: false, known: true });
  assert.deepEqual(unconfigured.models, []);
  assert.doesNotMatch(fs.readFileSync(credentialsFile, 'utf8'), new RegExp(CREDENTIAL_REF));

  await rpc('shutdown');
  await new Promise(resolve => child.once('exit', resolve));
  if (/did not activate|unmatched|not matched/i.test(stderr)) throw new Error(`dsh patch warning: ${stderr.slice(-2000)}`);
  process.stdout.write(`DSH startup to initialize response: ${startupMs} ms\n`);
  finish();
} catch (error) {
  finish(error);
}
