// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The MCP adapter's read-only mode: the delivery and decision tools must be
// absent from tools/list (absence is a boundary a prompt cannot argue past) and
// refused by tools/call for a client that ignores the list, while the Core's read
// tools stay available.
//
// Nothing wires this up yet. `agentToolServer` starts the adapter without
// `--readonly`, so every runtime -- including DSH, whose subagents share the
// parent's mount -- gets the full surface. The capability is here because the
// only place a subagent could be narrowed is DSH's own per-agent MCP
// configuration, which does not exist; see the known-limits section of
// docs/dsh-runtime.md. The tests below are what makes the mode trustworthy for
// whenever that lands.

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { startActionApi, stopActionApi } from '../src/actions/api.mjs';
import { createMcpHandler } from '../src/actions/mcp.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { Store } from '../src/store.mjs';
import { fileURLToPath } from 'node:url';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

const TOKEN = 'runtime-token';
const OWNER = { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } };
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const mcpPath = fileURLToPath(new URL('../src/actions/mcp.mjs', import.meta.url));

// The default withheld set, spelled out here so that shrinking it fails this
// test instead of silently widening what a scoped child agent can do.
const DEFAULT_WRITE_TOOLS = [
  'send_message', 'notify_owner', 'reply_message', 'react_to_message', 'retract_message',
  'stay_silent', 'create_timer', 'cancel_timer', 'finish_timer_occurrence', 'read_meeting_inbox',
];

// Read tools every lark-only instance offers; none of them may be withheld.
const READ_TOOLS = [
  'list_chats', 'list_chat_messages', 'search_messages', 'list_thread_messages',
  'get_message_media', 'read_openviking_session_context',
];

function setup(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-mcp-readonly-'));
  const workspace = path.join(directory, 'workspace');
  fs.mkdirSync(workspace);
  const store = new Store(path.join(directory, 'state.sqlite3'), logger);
  store.initializeCognition('codex', TOKEN);
  const connectors = fakeConnectors({ ids: ['lark'], owners: { lark: OWNER } });
  const service = new ActionService({
    config: { workspace, tempDir: path.join(directory, 'tmp'), maxReplyChars: 1_000, channel: 'lark' },
    store, connectors, logger,
  });
  t.after(() => {
    store.close();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const inbound = (fields) => store.ingest(chatEvent({ chatType: 'group', ...fields })).inbound;
  return { directory, connectors, service, inbound };
}

async function serve(t) {
  const state = setup(t);
  const socketPath = path.join(state.directory, 'actions.sock');
  const server = await startActionApi({ socketPath, service: state.service, logger });
  t.after(() => stopActionApi(server));
  const endpoint = { socketPath, authority: TOKEN, client: 'mcp' };
  const listed = async (handlerOptions) => {
    const handle = createMcpHandler(endpoint, handlerOptions);
    const response = await handle({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
    return response.result.tools.map((tool) => tool.name);
  };
  const call = async (handlerOptions, id, name, input = {}) => {
    const handle = createMcpHandler(endpoint, handlerOptions);
    return handle({ jsonrpc: '2.0', id, method: 'tools/call', params: { name, arguments: input } });
  };
  return { ...state, socketPath, listed, call };
}

describe('the read-only MCP instance a DSH child agent gets', () => {
  it('withholds every default delivery tool from tools/list and keeps the read tools', async (t) => {
    const { listed } = await serve(t);
    const full = await listed();
    // The list is real before the boundary is applied to it.
    assert.ok(full.includes('send_message'), full.join(', '));
    assert.ok(full.includes('list_chats'), full.join(', '));

    const readonly = await listed({ readOnly: true });
    // Exactly the default delivery tools that exist are gone, nothing else.
    assert.deepEqual(
      full.filter((name) => !readonly.includes(name)).sort(),
      full.filter((name) => DEFAULT_WRITE_TOOLS.includes(name)).sort(),
    );
    assert.ok(readonly.includes('send_message') === false, 'send_message must not be callable');
    for (const name of READ_TOOLS) assert.ok(readonly.includes(name), `${name} must stay listed`);

    // An explicit readOnly: false is the daemon's own surface, unchanged.
    assert.deepEqual(await listed({ readOnly: false }), full);
  });

  it('refuses tools/call for a withheld name instead of delivering', async (t) => {
    const { call, connectors, inbound } = await serve(t);
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    const send = { chat_id: 'oc_chat12345678', markdown: 'hi', decision_id: 'message:om_decision123456' };

    const refused = await call({ readOnly: true }, 2, 'send_message', send);
    assert.equal(refused.result.isError, true);
    assert.match(refused.result.content[0].text, /cannot call send_message/);
    // A withheld name is refused whether or not this instance lists it.
    const silent = await call({ readOnly: true }, 3, 'stay_silent', { decision_id: 'message:om_decision123456' });
    assert.equal(silent.result.isError, true);
    assert.equal(connectors.calls.length, 0, 'a withheld tool must never reach the connector');

    // The same request through the daemon's own surface is delivered: the
    // refusal above is the read-only boundary, not a broken tool.
    const delivered = await call({}, 4, 'send_message', send);
    assert.match(delivered.result.content[0].text, /Delivered message/);
    assert.equal(delivered.result.structuredContent.message_id, 'om_delivered123456');
    assert.equal(connectors.calls.length, 1);
  });

  it('withholds the caller\'s own list instead of a fixed one when writeTools is given', async (t) => {
    const { listed, call } = await serve(t);
    const readonly = await listed({ readOnly: true, writeTools: ['list_chats'] });
    assert.ok(readonly.includes('list_chats') === false, 'the named tool must be withheld');
    assert.ok(readonly.includes('send_message'), 'an unnamed tool must stay listed');
    const refused = await call({ readOnly: true, writeTools: ['list_chats'] }, 5, 'list_chats', {});
    assert.equal(refused.result.isError, true);
    assert.match(refused.result.content[0].text, /cannot call list_chats/);
  });

  it('honours a custom writeTools list even when readOnly is off', async (t) => {
    // readOnly is the switch: a writeTools list alone withholds nothing, so a
    // caller cannot half-configure the boundary by accident.
    const { listed, call, inbound, connectors } = await serve(t);
    const names = await listed({ writeTools: ['send_message'] });
    assert.ok(names.includes('send_message'));
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    const delivered = await call({ writeTools: ['send_message'] }, 6, 'send_message', {
      chat_id: 'oc_chat12345678', markdown: 'hi', decision_id: 'message:om_decision123456',
    });
    assert.match(delivered.result.content[0].text, /Delivered message/);
    assert.equal(connectors.calls.length, 1);
  });
  it('passes --readonly and --write-tools through the stdio adapter', async (t) => {
    // How a runtime actually gets the boundary: the adapter's own argument
    // parsing, not a caller of createMcpHandler.
    const { socketPath } = await serve(t);
    const run = (args, requests) => new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [mcpPath, '--socket', socketPath, '--authority', TOKEN, ...args], {
        env: { PATH: process.env.PATH },
      });
      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (chunk) => { stdout += chunk; });
      child.stderr.on('data', (chunk) => { stderr += chunk; });
      child.on('error', reject);
      child.on('close', (code) => resolve({
        code,
        responses: stdout.trim().split('\n').filter(Boolean).map(JSON.parse).sort((a, b) => a.id - b.id),
        stderr,
      }));
      child.stdin.end(requests.map((request, index) => JSON.stringify({ jsonrpc: '2.0', id: index, ...request })).join('\n') + '\n');
    });

    const readonly = await run(['--readonly'], [{ method: 'tools/list' }]);
    assert.equal(readonly.code, 0, readonly.stderr);
    assert.deepEqual(readonly.responses.map((response) => response.error).filter(Boolean), [],
      'the adapter answered tools/list with an error');
    const names = readonly.responses[0].result.tools.map((tool) => tool.name);
    assert.equal(names.includes('send_message'), false);
    assert.equal(names.includes('reply_message'), false);
    assert.ok(names.includes('list_chats'));

    // A bare `--readonly` must not swallow the next option as its value, and
    // --write-tools replaces the withheld names.
    const custom = await run(['--readonly', '--write-tools', 'list_chats,send_message'], [{ method: 'tools/list' }]);
    assert.equal(custom.code, 0, custom.stderr);
    assert.deepEqual(custom.responses.map((response) => response.error).filter(Boolean), [],
      'the adapter answered tools/list with an error');
    const customNames = custom.responses[0].result.tools.map((tool) => tool.name);
    assert.equal(customNames.includes('list_chats'), false);
    assert.equal(customNames.includes('send_message'), false);
    assert.ok(customNames.includes('reply_message'), 'an unnamed delivery tool stays listed');
  });
});
