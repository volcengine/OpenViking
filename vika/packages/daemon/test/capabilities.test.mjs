// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { CoreCapabilities } from '../src/core-capabilities.mjs';
const fixture = fileURLToPath(new URL('./fixtures/capability-mcp.mjs', import.meta.url));
function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'capabilities-'));
  const skill = path.join(root, 'skill'); fs.mkdirSync(skill);
  fs.writeFileSync(path.join(skill, 'SKILL.md'), '---\nname: example\n---\nUse the echo tool.');
  const log = path.join(root, 'calls');
  const file = path.join(root, 'config.json');
  fs.writeFileSync(file, JSON.stringify({ skills: { example: { directory: skill, description: 'Example skill' } }, mcpServers: { fixture: { command: process.execPath, args: [fixture], env: { CALL_LOG: log }, tools: ['echo'] } } }));
  const capabilities = new CoreCapabilities(file, root);
  t.after(async () => { await capabilities.close(); fs.rmSync(root, { recursive: true, force: true }); });
  return { root, skill, log, file, capabilities };
}
test('skills return bounded source with hash and refuse traversal and symlink escape', t => {
  const { capabilities: c, root, skill } = setup(t);
  assert.equal(c.list().skills[0].id, 'example');
  assert.match(c.readSkill('example').sha256, /^[a-f0-9]{64}$/);
  assert.throws(() => c.readSkill('example', '../config.json'));
  assert.throws(() => c.readSkill('__proto__'));
  fs.writeFileSync(path.join(skill, 'large.md'), 'x'.repeat(128 * 1024 + 1));
  assert.throws(() => c.readSkill('example', 'large.md'), /128 KiB/);
  const outside = path.join(root, 'outside');
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, 'secret.md'), 'not a skill resource');
  fs.symlinkSync(outside, path.join(skill, 'escape'), 'junction');
  assert.throws(() => c.readSkill('example', 'escape/secret.md'), /escapes/);
});
test('real MCP initialization, allowlist, error preservation, no retry and shutdown', async t => {
  const { capabilities: c, log } = setup(t);
  const [a, b] = await Promise.all([c.listMcp('fixture'), c.listMcp('fixture')]);
  assert.deepEqual(a.map(tool => tool.name), ['echo']); assert.deepEqual(a, b);
  await assert.rejects(c.callMcp('fixture', 'hidden', {}, () => {}), /not allowed/);
  await assert.rejects(c.callMcp('fixture', 'echo', {}, () => { throw new Error('stale'); }), /stale/);
  assert.equal(fs.existsSync(log), false);
  const result = await c.callMcp('fixture', 'echo', { fail: true }, () => {});
  assert.equal(result.isError, true);
  assert.equal(fs.readFileSync(log, 'utf8'), 'echo\n');
  await c.close();
  await assert.rejects(c.listMcp('fixture'), /closed/);
});
test('configuration errors are rejected before starting any server', t => {
  const { root, file } = setup(t);
  fs.writeFileSync(file, JSON.stringify({ mcpServers: { bad: { command: 'node', tools: ['echo'] } } }));
  assert.throws(() => new CoreCapabilities(file, root), /Invalid MCP/);
});

test('stdio servers receive explicit environment but not unrelated parent secrets', async t => {
  const { capabilities: c } = setup(t);
  const previous = process.env.CAPABILITY_UNRELATED_SECRET;
  process.env.CAPABILITY_UNRELATED_SECRET = 'synthetic-parent-only';
  t.after(() => {
    if (previous === undefined) delete process.env.CAPABILITY_UNRELATED_SECRET;
    else process.env.CAPABILITY_UNRELATED_SECRET = previous;
  });
  c.servers.fixture.env.CAPABILITY_EXPLICIT = 'configured';
  const result = await c.callMcp('fixture', 'echo', { envProbe: true }, () => {});
  assert.deepEqual(JSON.parse(result.content[0].text), { explicit: 'configured', inheritedSecret: null });
});

test('shutdown cancels initialization instead of waiting for the handshake timeout', { timeout: 5000 }, async t => {
  const { capabilities: c, log } = setup(t);
  c.servers.fixture.env.FIXTURE_MODE = 'hang-init';
  const pending = assert.rejects(c.listMcp('fixture'), /could not initialize/);
  const deadline = Date.now() + 2000;
  while (!fs.existsSync(log) && Date.now() < deadline) await delay(10);
  assert.equal(fs.readFileSync(log, 'utf8'), 'initializing');
  await c.close();
  await pending;
  await assert.rejects(c.listMcp('fixture'), /closed/);
});

test('a disconnected write is not retried or respawned on the next call', async t => {
  const { capabilities: c, log } = setup(t);
  await assert.rejects(c.callMcp('fixture', 'echo', { disconnect: true }, () => {}), /side effects may have occurred/);
  await assert.rejects(c.callMcp('fixture', 'echo', {}, () => {}), /Do not automatically retry/);
  assert.equal(fs.readFileSync(log, 'utf8'), 'echo\n');
});
