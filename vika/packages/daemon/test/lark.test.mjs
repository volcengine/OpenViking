// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { LarkClient, SUBSCRIBED_EVENT_TYPES, renderMentions } from '../src/lark.mjs';
import { sanitizeNonArkChildEnv } from '../src/process-utils.mjs';
import { LARK_VIDEO_PREP_MS, LARK_VIDEO_STEP_TIMEOUT_MS, unsendableVideoError } from '../src/lark-video.mjs';
import { ConnectorProcess } from '../src/connectors/process.mjs';

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

it('gives a Lark video send its cover extraction time on top of the send timeout', () => {
  const lark = new ConnectorProcess('lark', { sendTimeoutMs: 5000 }, null, quietLogger);
  Object.assign(lark, { child: {}, checked: true, request: (_operation, _payload, timeoutMs) => timeoutMs });
  const budget = (name) => lark.invoke('message.send', { content: { type: 'file', path: `/frozen/${name}`, name } });
  assert.equal(budget('demo.mp4'), 5000 + LARK_VIDEO_PREP_MS + 1000);
  assert.equal(budget('demo.mov'), 5000 + LARK_VIDEO_PREP_MS + 1000);
  assert.equal(budget('report.pdf'), 5000);
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

  it('sends .mp4/.mov as playable media with an ffmpeg cover from cwd-relative paths', async () => {
    const { lark, calls, tempDir } = client((args) => {
      if (args.includes('-frames:v')) fs.writeFileSync(args.at(-1), 'jpeg');
      return { message_id: 'om_video12345678' };
    });
    const sent = await lark.replyMessage({ message_id: 'om_target1234567', reply_in_thread: true,
      content: { type: 'file', path: path.join(tempDir, 'demo.MOV'), name: 'demo.MOV' }, idempotency_key: 'kv' });
    assert.deepEqual(sent, { message_id: 'om_video12345678' });
    const ffmpeg = calls.find(({ args }) => args.includes('-frames:v'));
    assert.deepEqual(ffmpeg.args.slice(0, 2), ['-y', '-ss']);
    assert.ok(ffmpeg.options.timeoutMs <= LARK_VIDEO_STEP_TIMEOUT_MS);
    const send = calls.at(-1);
    assert.equal(send.options.cwd, tempDir);
    assert.ok(send.args.includes('--reply-in-thread'));
    assert.equal(send.args[send.args.indexOf('--video') + 1], './demo.MOV');
    assert.match(send.args[send.args.indexOf('--video-cover') + 1], /^\.\/\.video-[^/]+\/cover\.jpg$/);
    assert.ok(!send.args.includes('--file'));
    assert.equal(send.args[send.args.indexOf('--idempotency-key') + 1], 'kv');
    assert.deepEqual(fs.readdirSync(tempDir), [], 'temporary cover is removed');
  });

  it('sends a video with a placeholder cover when ffmpeg is missing or fails', async () => {
    const warnings = [];
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-im-'));
    const calls = [];
    const lark = new LarkClient({ profile: 'bot-profile', larkCliBin: '/fake/lark-cli', projectDir: '/project',
      tempDir, sendTimeoutMs: 5000 }, {
      async run(command, args, options) {
        if (command === 'ffprobe' || command === 'ffmpeg') throw Object.assign(new Error(`spawn ${command} ENOENT`), { code: 'ENOENT' });
        const cover = args[args.indexOf('--video-cover') + 1];
        calls.push({ args, cover: fs.readFileSync(path.join(options.cwd, cover)) });
        return { code: 0, timedOut: false, stdout: JSON.stringify({ ok: true, data: { message_id: 'om_video12345678' } }), stderr: '' };
      },
    }, { ...quietLogger, warn: (event, fields) => warnings.push({ event, fields }) });
    const sent = await lark.sendMessage({ chat_id: 'oc_chat12345678',
      content: { type: 'file', path: path.join(tempDir, 'demo.mp4'), name: 'demo.mp4' }, idempotency_key: 'kp' });
    assert.equal(sent.message_id, 'om_video12345678');
    assert.match(calls[0].args[calls[0].args.indexOf('--video-cover') + 1], /^\.\/\.video-[^/]+\/cover\.png$/);
    assert.deepEqual([...calls[0].cover.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 'a PNG cover');
    assert.deepEqual(warnings.map(({ event, fields }) => [event, fields.error]),
      [['lark_video_cover_placeholder', 'spawn ffmpeg ENOENT']]);
    assert.deepEqual(fs.readdirSync(tempDir), []);
  });

  it('reports the ffmpeg error line when a frame cannot be read', async () => {
    const warnings = [];
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-im-'));
    const lark = new LarkClient({ profile: 'bot-profile', larkCliBin: '/fake/lark-cli', projectDir: '/project',
      tempDir, sendTimeoutMs: 5000 }, {
      async run(command) {
        if (command === 'ffprobe') return { code: 0, timedOut: false, stdout: '0.2\n', stderr: '' };
        if (command === 'ffmpeg') return { code: 1, timedOut: false, stdout: '', stderr: 'ffmpeg version x\nclip.mp4: Invalid data found when processing input\n' };
        return { code: 0, timedOut: false, stdout: JSON.stringify({ ok: true, data: { message_id: 'om_video12345678' } }), stderr: '' };
      },
    }, { ...quietLogger, warn: (event, fields) => warnings.push(fields.error) });
    await lark.sendMessage({ chat_id: 'oc_chat12345678',
      content: { type: 'file', path: path.join(tempDir, 'clip.mp4'), name: 'clip.mp4' }, idempotency_key: 'ke' });
    assert.deepEqual(warnings, ['ffmpeg exit 1: clip.mp4: Invalid data found when processing input']);
  });

  it('returns a clear error without a --file fallback when the video send is rejected', async () => {
    const calls = [];
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-im-'));
    const lark = new LarkClient({ profile: 'bot-profile', larkCliBin: '/fake/lark-cli', projectDir: '/project',
      tempDir, sendTimeoutMs: 5000 }, {
      async run(command, args, options) {
        calls.push({ args, options });
        if (args.includes('-frames:v')) fs.writeFileSync(args.at(-1), 'jpeg');
        if (args.includes('--video')) return { code: 1, timedOut: false, stdout: JSON.stringify({ ok: false, error: { message: 'unsupported msg_type' } }), stderr: '' };
        return { code: 0, timedOut: false, stdout: JSON.stringify({ ok: true, data: { message_id: 'om_file123456789' } }), stderr: '' };
      },
    }, quietLogger);
    await assert.rejects(lark.sendMessage({ chat_id: 'oc_chat12345678',
      content: { type: 'file', path: path.join(tempDir, 'demo.mp4'), name: 'demo.mp4' }, idempotency_key: 'kf' }),
    /send video failed: unsupported msg_type.*Drive/);
    assert.ok(!calls.some(({ args }) => args.includes('--file')));
    assert.deepEqual(fs.readdirSync(tempDir), []);
  });

  it('sends other video-like extensions such as .m4v as plain files without ffmpeg', async () => {
    const { lark, calls, tempDir } = client(() => ({ message_id: 'om_file123456789' }));
    await lark.sendMessage({ chat_id: 'oc_chat12345678',
      content: { type: 'file', path: path.join(tempDir, 'clip.m4v'), name: 'clip.m4v' }, idempotency_key: 'km' });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].args[calls[0].args.indexOf('--file') + 1], './clip.m4v');
  });

  it('names the containers Lark cannot send at all, with a way forward', () => {
    for (const name of ['clip.mkv', 'clip.WEBM', 'clip.avi']) {
      assert.match(unsendableVideoError(`/frozen/${name}`).message, new RegExp(`cannot send ${name.replace('.', '\\.')}.*\\.mp4.*Drive`));
    }
    for (const name of ['clip.mp4', 'clip.mov', 'clip.m4v', 'report.pdf']) assert.equal(unsendableVideoError(`/frozen/${name}`), null);
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
  it('subscribes to IM, card callbacks, document comments, and the three VC push event types', () => {
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
