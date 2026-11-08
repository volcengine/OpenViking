// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
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
  if (process.platform !== 'win32') {
    fs.symlinkSync(path.join(root, 'config.json'), path.join(skill, 'escape.md'));
    assert.throws(() => c.readSkill('example', 'escape.md'), /escapes/);
  }
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
