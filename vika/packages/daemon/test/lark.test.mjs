// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { LarkClient, SUBSCRIBED_EVENT_TYPES, renderMentions } from '../src/lark.mjs';
import { sanitizeNonArkChildEnv } from '../src/process-utils.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {} };

it('updates an answered card using bot message patch without callback credentials', async () => {
  const calls = [];
  const client = new LarkClient({ profile: 'test-profile', larkCliBin: '/fake/lark-cli',
    projectDir: '/tmp', sendTimeoutMs: 5000 }, {
    async run(command, args, options) {
      calls.push({ command, args, options });
      return { code: 0, stdout: JSON.stringify({ ok: true, data: {} }), stderr: '' };
    },
  }, quietLogger);
  const card = { elements: [{ tag: 'div', text: { tag: 'plain_text', content: '已选择：继续' } }] };
  await client.updateCard('om_answered123456', card);
  assert.deepEqual(calls[0].args, ['--profile', 'test-profile', 'im', 'messages', 'patch', '--as', 'bot',
    '--message-id', 'om_answered123456', '--data', JSON.stringify({ content: JSON.stringify(card) }), '--format', 'json']);
  assert.equal(calls[0].options.timeoutMs, 5000);
  client.config.dryRun = true;
  assert.equal((await client.updateCard('om_answered123456', card)).dryRun, true);
  assert.equal(calls.length, 1);
});

describe('Lark IM operations', () => {
  function client(respond = () => ({})) {
    const calls = [];
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-im-'));
    const lark = new LarkClient({ profile: 'bot-profile', larkCliBin: '/fake/lark-cli', projectDir: '/project',
      tempDir, sendTimeoutMs: 5000 }, {
      async run(command, args, options) {
        calls.push({ args, options });
        const data = respond(args, options);
        return { code: 0, timedOut: false, stdout: JSON.stringify({ ok: true, data }), stderr: '' };
      },
    }, quietLogger);
    return { lark, calls, tempDir };
  }

  it('sends and replies with the fixed bot profile and the Core idempotency key', async () => {
    const { lark, calls } = client(() => ({ message_id: 'om_sent123456789' }));
    const sent = await lark.sendMessage({ chat_id: 'oc_chat12345678', content: { type: 'markdown', text: 'hi' }, idempotency_key: 'ld_key' });
    assert.deepEqual(sent, { message_id: 'om_sent123456789' });
    assert.deepEqual(calls[0].args, ['--profile', 'bot-profile', 'im', '+messages-send', '--as', 'bot',
      '--chat-id', 'oc_chat12345678', '--markdown', 'hi', '--idempotency-key', 'ld_key', '--format', 'json']);
    await lark.replyMessage({ message_id: 'om_target1234567', reply_in_thread: true,
      content: { type: 'file_key', key: 'file_existing123' }, idempotency_key: 'ld_key2' });
    assert.deepEqual(calls[1].args.slice(3), ['+messages-reply', '--as', 'bot', '--message-id', 'om_target1234567',
      '--reply-in-thread', '--file', 'file_existing123', '--idempotency-key', 'ld_key2', '--format', 'json']);
  });

  it('uploads a frozen file from its own directory and sends images as images', async () => {
    const { lark, calls, tempDir } = client(() => ({ message_id: 'om_sent123456789' }));
    for (const name of ['chart.png', 'report.pdf']) {
      await lark.sendMessage({ chat_id: 'oc_chat12345678', content: { type: 'file', path: path.join(tempDir, name), name }, idempotency_key: 'k' });
    }
    assert.deepEqual(calls.map(({ args, options }) => [args[args.indexOf('--chat-id') + 2], args[args.indexOf('--chat-id') + 3], options.cwd]), [
      ['--image', './chart.png', tempDir], ['--file', './report.pdf', tempDir],
    ]);
  });

  it('renders inline and prefixed mentions without ever sending @all', () => {
    assert.equal(renderMentions('@Alice owns this', [{ id: 'ou_alice', name: 'Alice' }, { id: 'ou_bob' }]),
      '<at user_id="ou_bob"></at> <at user_id="ou_alice"></at> owns this');
    assert.equal(renderMentions('hi <at user_id="all">everyone</at>', []), 'hi 所有人');
  });

  it('reacts, removes a reaction and retracts with structured payloads', async () => {
    const { lark, calls } = client((args) => (args.includes('create') ? { reaction_id: 'reaction_1' } : {}));
    assert.deepEqual(await lark.reactMessage({ message_id: 'om_target1234567', emoji_type: 'OK' }), { reaction_id: 'reaction_1' });
    await lark.deleteReaction({ message_id: 'om_target1234567', reaction_id: 'reaction_1' });
    await lark.retractMessage({ message_id: 'om_sent123456789' });
    assert.deepEqual(calls.map(({ args }) => args.slice(3)), [
      ['reactions', 'create', '--as', 'bot', '--params', '{"message_id":"om_target1234567"}',
        '--data', '{"reaction_type":{"emoji_type":"OK"}}', '--format', 'json'],
      ['reactions', 'delete', '--as', 'bot', '--message-id', 'om_target1234567', '--reaction-id', 'reaction_1', '--format', 'json'],
      ['messages', 'delete', '--as', 'bot', '--params', '{"message_id":"om_sent123456789"}', '--format', 'json', '--yes'],
    ]);
  });

  it('lists every joined-chat page and filters names locally', async () => {
    const pages = [
      { chats: [{ chat_id: 'oc_first1234567', name: 'Alpha\nteam', chat_status: 'normal' }], has_more: true, page_token: 'p2' },
      { chats: [{ chat_id: 'oc_second123456', name: 'Beta', chat_status: 'normal' }], has_more: false },
    ];
    const { lark, calls } = client(() => pages.shift());
    const result = await lark.listChats({ query: 'alpha' });
    assert.deepEqual(result, { chats: [{ chat_id: 'oc_first1234567', name: 'Alpha team', chat_status: 'normal' }], has_more: false });
    assert.ok(calls[1].args.includes('p2'));
  });

  it('downloads a message resource into the private media directory', async () => {
    const { lark, calls, tempDir } = client((args, options) => {
      fs.writeFileSync(path.join(options.cwd, `${args[args.indexOf('--output') + 1]}.png`), 'png');
      return { saved_path: `${args[args.indexOf('--output') + 1]}.png` };
    });
    const { media: [item] } = await lark.getMedia({ message_id: 'om_image12345678', key: 'img_abcdefgh1234' });
    assert.equal(path.dirname(item.path), path.join(tempDir, 'media'));
    assert.deepEqual([item.kind, item.mime_type, item.size], ['image', 'image/png', 3]);
    assert.deepEqual(calls[0].args.slice(3, 13), ['+messages-resources-download', '--as', 'bot',
      '--message-id', 'om_image12345678', '--file-key', 'img_abcdefgh1234', '--type', 'image', '--output']);
  });
});

describe('non-Ark child environment', () => {
  it('strips every Ark credential and session alias', () => {
    const environment = sanitizeNonArkChildEnv({
      PATH: '/bin',
      LARK_APP_ID: 'keep-for-lark',
      ARK_API_KEY: 'remove',
      ARK_SESSION_ID: 'remove',
      VIKA_VOLC_SESSION_ID: 'remove',
      SESSION_ID: 'remove',
    });
    assert.equal(environment.PATH, '/bin');
    assert.equal(environment.LARK_APP_ID, 'keep-for-lark');
    assert.equal(environment.ARK_API_KEY, undefined);
    assert.equal(environment.ARK_SESSION_ID, undefined);
    assert.equal(environment.VIKA_VOLC_SESSION_ID, undefined);
    assert.equal(environment.SESSION_ID, undefined);
  });
});

describe('Lark chat lookup', () => {
  it('gets a group name with the schema-verified bot chat command', async () => {
    const calls = [];
    const registry = {
      async run(command, args, options) {
        calls.push({ command, args, options });
        return {
          code: 0,
          timedOut: false,
          stdout: JSON.stringify({ data: { name: '项目讨论群' } }),
          stderr: '',
        };
      },
    };
    const client = new LarkClient({
      profile: 'test-profile',
      larkCliBin: '/fake/lark-cli',
      projectDir: '/tmp',
    }, registry, quietLogger);
    assert.equal(await client.getChatName('oc-private-chat'), '项目讨论群');
    assert.deepEqual(calls[0].args, [
      '--profile', 'test-profile',
      'im', 'chats', 'get',
      '--as', 'bot',
      '--chat-id', 'oc-private-chat',
      '--format', 'json',
    ]);
    assert.equal(calls[0].options.timeoutMs, 15_000);
  });

  it('surfaces chat lookup permission failures for daemon retry handling', async () => {
    const registry = {
      async run() {
        return { code: 1, timedOut: false, stdout: '', stderr: 'permission denied' };
      },
    };
    const client = new LarkClient({
      profile: 'test-profile',
      larkCliBin: '/fake/lark-cli',
      projectDir: '/tmp',
    }, registry, quietLogger);
    await assert.rejects(() => client.getChatName('oc-private-chat'), /permission denied/);
  });
});

describe('Lark meeting transport', () => {
  it('subscribes to IM, card callbacks, doc comment notices, and the three VC push event types', () => {
    assert.deepEqual(SUBSCRIBED_EVENT_TYPES, [
      'im.message.receive_v1',
      'card.action.trigger',
      'drive.notice.comment_add_v1',
      'vc.bot.meeting_invited_v1',
      'vc.bot.meeting_activity_v1',
      'vc.bot.meeting_ended_v1',
    ]);
  });

  it('uses application identity for invite-correlated joins and cursor recovery', async () => {
    const calls = [];
    const registry = {
      async run(command, args, options) {
        calls.push({ command, args, options });
        if (args.includes('+meeting-join')) {
          return {
            code: 0,
            timedOut: false,
            stdout: JSON.stringify({ ok: true, data: { meeting: { id: '734000000000000001' } } }),
            stderr: '',
          };
        }
        if (args.includes('+meeting-leave')) {
          return {
            code: 0,
            timedOut: false,
            stdout: JSON.stringify({ ok: true, data: {} }),
            stderr: '',
          };
        }
        return {
          code: 0,
          timedOut: false,
          stdout: JSON.stringify({ ok: true, data: { events: [], page_token: 'next' } }),
          stderr: '',
        };
      },
    };
    const client = new LarkClient({
      profile: 'test-profile',
      larkCliBin: '/fake/lark-cli',
      projectDir: '/tmp',
      sendTimeoutMs: 5_000,
    }, registry, quietLogger);
    await client.joinMeeting({ meetingNumber: '123456789', callId: 'call-123' });
    await client.listMeetingEvents({ meetingId: '734000000000000001', pageToken: 'cursor' });
    await client.leaveMeeting('734000000000000001');
    assert.deepEqual(calls[0].args, [
      '--profile', 'test-profile', 'vc', '+meeting-join', '--as', 'bot',
      '--meeting-number', '123456789', '--call-id', 'call-123', '--format', 'json',
    ]);
    assert.deepEqual(calls[1].args, [
      '--profile', 'test-profile', 'vc', '+meeting-events', '--as', 'bot',
      '--meeting-id', '734000000000000001', '--page-token', 'cursor',
      '--page-all', '--format', 'json',
    ]);
    assert.deepEqual(calls[2].args, [
      '--profile', 'test-profile', 'vc', '+meeting-leave', '--as', 'bot',
      '--meeting-id', '734000000000000001', '--format', 'json',
    ]);
  });
});

describe('Lark subscriber process group', () => {
  async function waitUntilDead(pid, timeoutMs = 4_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try { process.kill(pid, 0); } catch (error) {
        if (error.code === 'ESRCH') return true;
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    return false;
  }

  it('reaps leftover group members when the subscriber leader exits', {
    skip: process.platform === 'win32',
  }, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-subscriber-tree-'));
    const pidFile = path.join(directory, 'child.pid');
    const fakeLark = path.join(directory, 'fake-lark.mjs');
    fs.writeFileSync(fakeLark, `#!/usr/bin/env node
      import { spawn } from 'node:child_process';
      import fs from 'node:fs';
      const args = process.argv.slice(2);
      if (args.includes('event')) {
        const child = spawn(process.execPath, ['-e', 'process.on("SIGTERM", () => {}); setInterval(() => {}, 1000)'], { stdio: 'ignore' });
        fs.writeFileSync(${JSON.stringify(pidFile)}, String(child.pid));
        setTimeout(() => process.exit(0), 50);
      } else {
        process.exit(0);
      }
    `, { mode: 0o700 });

    const unexpected = [];
    const client = new LarkClient({
      profile: 'test-profile',
      larkCliBin: fakeLark,
      projectDir: directory,
      maxEventBytes: 1_000_000,
    }, { register: (child) => child }, quietLogger);

    let descendantPid = 0;
    try {
      client.startSubscription(() => {}, (exit) => unexpected.push(exit));
      const deadline = Date.now() + 4_000;
      while (unexpected.length === 0 && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
      assert.equal(unexpected.length, 1, 'subscriber leader should exit on its own');
      assert.ok(fs.existsSync(pidFile), 'leader must have spawned a descendant');
      descendantPid = Number.parseInt(fs.readFileSync(pidFile, 'utf8'), 10);
      assert.equal(await waitUntilDead(descendantPid), true, 'descendant should die with the leader group');
    } finally {
      if (descendantPid) {
        try { process.kill(descendantPid, 'SIGKILL'); } catch {}
      }
      await client.stop();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
});
