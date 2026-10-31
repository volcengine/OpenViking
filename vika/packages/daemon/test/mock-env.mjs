// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Shared mock environment for harness e2e tests: a real PersonalAgentDaemon wired to a
// fake lark-cli (events fed dynamically through an NDJSON file) and a fake
// codex app-server (scriptable turn outcome, records everything it is told).
// OpenViking is a quiet fake client by default; pass { identity } to turn
// identity injection on and serve that text from it.
// No process.env mutation:
// every path the fake binaries need is embedded into the generated scripts.
//
// Operations, not plumbing:
//   const env = await startMockEnv();
//   env.receive(textMessage({ messageId: 'om-1', text: 'hi' }));
//   await env.waitForCodexInput(/om-1/);
//   env.replies(); env.codexInput(); env.codexSpawnArgs();
//   await env.stop();

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { getConfig, projectDir } from '../src/config.mjs';
import { createLogger } from '../src/logger.mjs';
import { PersonalAgentDaemon } from '../src/daemon.mjs';

// Raw Lark im.message.receive_v1 envelope with test defaults. Every field the
// normalizer reads can be overridden; `text` becomes the JSON content body.
export function textMessage({
  messageId,
  text,
  chatId = 'oc-e2e',
  chatType = 'p2p',
  senderId = 'ou-e2e',
  threadId = null,
  parentId = null,
  rootId = null,
  mentions = null,
  createTimeMs = Date.now(),
  eventId = `evt-${messageId}`,
}) {
  return {
    schema: '2.0',
    header: {
      event_id: eventId,
      event_type: 'im.message.receive_v1',
      tenant_key: 'tenant-e2e',
      create_time: String(createTimeMs),
    },
    event: {
      sender: { sender_id: { open_id: senderId }, sender_type: 'user' },
      message: {
        message_id: messageId,
        chat_id: chatId,
        chat_type: chatType,
        message_type: 'text',
        content: JSON.stringify({ text }),
        create_time: String(createTimeMs),
        ...(threadId ? { thread_id: threadId } : {}),
        ...(parentId ? { parent_id: parentId } : {}),
        ...(rootId ? { root_id: rootId } : {}),
        ...(mentions ? { mentions } : {}),
      },
    },
  };
}

// Raw Lark drive.notice.comment_add_v1 notice: ids only, as the platform
// sends it. The fake lark-cli answers the comment lookups for it (see
// fakeLarkSource): the reply `r-e2e` by ou_e2eCommenter1 reads 帮我看下这段.
export function docComment({
  commentId = 'c-e2e',
  replyId = 'r-e2e',
  fileToken = 'doxE2eToken',
  fromId = 'ou_e2eCommenter1',
  mentioned = true,
  eventId = `evt-${commentId}-${replyId}`,
  createTimeMs = Date.now(),
} = {}) {
  return {
    schema: '2.0',
    header: { event_id: eventId, event_type: 'drive.notice.comment_add_v1', tenant_key: 'tenant-e2e',
      create_time: String(createTimeMs) },
    event: {
      notice_meta: { file_type: 'docx', file_token: fileToken, from_user_id: { open_id: fromId },
        to_user_id: { open_id: 'ou_mockSelf123456' }, notice_type: 'add_comment' },
      comment_id: commentId, reply_id: replyId, is_mentioned: mentioned,
    },
  };
}

function writeExecutable(file, source) {
  fs.writeFileSync(file, `#!/usr/bin/env node\n${source}`, { mode: 0o700 });
}

// Fake lark-cli: --version/--help/doctor/auth status/bot info answer preflight
// (the bot is TestBot, ou_mockSelf123456); `event +subscribe`
// records its pid in SUBSCRIBER and tails EVENTS (NDJSON, appended by
// env.receive) from the start until SIGTERM; +messages-reply,
// +messages-send and drive +add-reply append their argv to REPLIES. A
// document comment reads as one open comment whose reply r-e2e asks
// 帮我看下这段, in a document titled E2E 文档. Everything else gets a
// generic ok JSON so best-effort lookups (roster, chat names) degrade quietly.
// Every call's argv goes to CALLS.
function fakeLarkSource(dir) {
  return `
    import fs from 'node:fs';
    const EVENTS = ${JSON.stringify(path.join(dir, 'events.ndjson'))};
    const REPLIES = ${JSON.stringify(path.join(dir, 'replies.ndjson'))};
    const SUBSCRIBER = ${JSON.stringify(path.join(dir, 'subscriber.pid'))};
    const CALLS = ${JSON.stringify(path.join(dir, 'lark-calls.ndjson'))};
    const args = process.argv.slice(2);
    fs.appendFileSync(CALLS, JSON.stringify(args) + '\\n');
    if (args.includes('--version')) { console.log('lark-cli 1.0.56'); process.exit(0); }
    if (args.includes('--help')) {
      console.log('--event-types --quiet +messages-reply +messages-send +chat-list');
      process.exit(0);
    }
    if (args.includes('doctor')) { console.log(JSON.stringify({ ok: true })); process.exit(0); }
    if (args.includes('auth') && args.includes('status')) {
      console.log(JSON.stringify({ identities: { user: { openId: 'ou_mockOwner12345' } } }));
      process.exit(0);
    }
    if (args.includes('/open-apis/bot/v3/info')) {
      console.log(JSON.stringify({ ok: true, identity: 'bot', data: { app_name: 'TestBot', open_id: 'ou_mockSelf123456' } }));
      process.exit(0);
    }
    if (args.includes('+messages-reply') || args.includes('+messages-send')) {
      fs.appendFileSync(REPLIES, JSON.stringify(args) + '\\n');
      console.log(JSON.stringify({ ok: true, data: { message_id: 'om-mock-out' } }));
      process.exit(0);
    }
    if (args.includes('+batch-query-comments')) {
      const commentId = args[args.indexOf('--comment-ids') + 1];
      console.log(JSON.stringify({ ok: true, data: { items: [{ comment_id: commentId, is_whole: false, is_solved: false,
        quote: '第二季度目标', reply_list: { replies: [{ reply_id: 'r-e2e', user_id: 'ou_e2eCommenter1', content: { elements: [
          { type: 'person', person: { user_id: 'ou_mockSelf123456' } }, { type: 'text_run', text_run: { text: ' 帮我看下这段' } },
        ] } }] } }] } }));
      process.exit(0);
    }
    if (args.includes('metas')) {
      console.log(JSON.stringify({ ok: true, data: { metas: [{ doc_token: 'doxE2eToken', title: 'E2E 文档',
        url: 'https://example.feishu.cn/docx/doxE2eToken' }] } }));
      process.exit(0);
    }
    if (args.includes('+get-user')) {
      console.log(JSON.stringify({ ok: true, data: { user: { name: 'Commenter' } } }));
      process.exit(0);
    }
    if (args.includes('+add-reply')) {
      fs.appendFileSync(REPLIES, JSON.stringify(args) + '\\n');
      console.log(JSON.stringify({ ok: true, data: { comment_id: 'c-e2e', reply_id: 'r-e2e-out' } }));
      process.exit(0);
    }
    if (args.includes('+chat-members-list')) {
      console.log(JSON.stringify({ ok: true, data: { users: [], bots: [] } }));
      process.exit(0);
    }
    if (args.includes('event')) {
      fs.writeFileSync(SUBSCRIBER, String(process.pid));
      // ponytail: 20ms file polling; appends are small enough to arrive whole.
      let offset = 0;
      const timer = setInterval(() => {
        let stat;
        try { stat = fs.statSync(EVENTS); } catch { return; }
        if (stat.size <= offset) return;
        const fd = fs.openSync(EVENTS, 'r');
        const buffer = Buffer.alloc(stat.size - offset);
        fs.readSync(fd, buffer, 0, buffer.length, offset);
        fs.closeSync(fd);
        offset = stat.size;
        process.stdout.write(buffer);
      }, 20);
      const bye = () => { clearInterval(timer); process.exit(0); };
      process.on('SIGTERM', bye);
      process.on('SIGINT', bye);
    } else {
      console.log(JSON.stringify({ ok: true, data: {} }));
    }
  `;
}

// Fake codex app-server: newline JSON-RPC over stdio. Appends every turn input
// to INPUT_LOG and its own argv to SPAWN_LOG; turn outcome is fixed per env:
// 'complete' | 'fail' (with FAIL_BLOB) | 'ack-only' | 'silent' (calls
// stay_silent on the newest event= of the turn input over the daemon's agent
// tool socket, as a real runtime's MCP adapter would, then completes) |
// 'reply' (likewise answers the newest msg= with reply_message "mock reply").
const ACTION_CLIENT = fileURLToPath(new URL('../src/actions/client.mjs', import.meta.url));

function fakeCodexSource(dir, { behavior, failBlob }) {
  return `
    import fs from 'node:fs';
    import readline from 'node:readline';
    import { actionEndpoint, callTool } from ${JSON.stringify(ACTION_CLIENT)};
    const INPUT_LOG = ${JSON.stringify(path.join(dir, 'codex-input.log'))};
    const SPAWN_LOG = ${JSON.stringify(path.join(dir, 'codex-spawns.ndjson'))};
    const BEHAVIOR = ${JSON.stringify(behavior)};
    const FAIL_BLOB = ${JSON.stringify(failBlob)};
    fs.appendFileSync(SPAWN_LOG, JSON.stringify(process.argv.slice(2)) + '\\n');
    const send = (payload) => process.stdout.write(JSON.stringify(payload) + '\\n');
    const rl = readline.createInterface({ input: process.stdin });
    let turnSeq = 0;
    rl.on('line', (line) => {
      let msg;
      try { msg = JSON.parse(line); } catch { return; }
      if (msg.method === 'initialize') { send({ id: msg.id, result: {} }); return; }
      if (msg.method === 'initialized') { return; }
      if (msg.method === 'thread/start' || msg.method === 'thread/resume') {
        send({ id: msg.id, result: { thread: { id: 'thread-mock' } } });
        send({ method: 'thread/started', params: { thread: { id: 'thread-mock' } } });
        return;
      }
      if (msg.method === 'model/list') {
        send({ id: msg.id, result: { data: [
          { id: 'gpt-5.6-sol', supportedReasoningEfforts: ['low', 'medium', 'high', 'xhigh'] },
        ] } });
        return;
      }
      if (msg.method === 'turn/start') {
        const text = msg.params.input.map((part) => part.text).join('');
        fs.appendFileSync(INPUT_LOG, JSON.stringify({ method: 'turn/start', text }) + '\\n');
        send({ id: msg.id, result: {} });
        const turnId = 'turn-' + (++turnSeq);
        send({ method: 'turn/started', params: { turn: { id: turnId } } });
        if (BEHAVIOR === 'complete') {
          send({ method: 'turn/completed', params: { turn: { id: turnId, status: 'completed' } } });
        } else if (BEHAVIOR === 'silent' || BEHAVIOR === 'reply') {
          const decision = [...text.matchAll(/ event=(\\S+)/g)].at(-1)?.[1];
          const target = [...text.matchAll(/ msg=(\\S+)/g)].at(-1)?.[1];
          const done = () => send({ method: 'turn/completed', params: { turn: { id: turnId, status: 'completed' } } });
          if (!decision) { done(); return; }
          const [tool, input] = BEHAVIOR === 'silent' ? ['stay_silent', { decision_id: decision, reason: 'mock' }]
            : ['reply_message', { decision_id: decision, message_id: target, markdown: 'mock reply' }];
          callTool(actionEndpoint('fake-codex'), tool, input)
            .then(done, (error) => {
              fs.appendFileSync(INPUT_LOG, JSON.stringify({ method: 'tool-error', text: String(error.message) }) + '\\n');
              done();
            });
        } else if (BEHAVIOR === 'fail') {
          send({ method: 'turn/completed', params: { turn: { id: turnId, status: 'failed', error: { message: FAIL_BLOB } } } });
        }
        return;
      }
      if (msg.method === 'turn/steer') {
        const text = msg.params.input.map((part) => part.text).join('');
        fs.appendFileSync(INPUT_LOG, JSON.stringify({ method: 'turn/steer', text }) + '\\n');
        send({ id: msg.id, result: {} });
        return;
      }
      if (msg.method === 'turn/interrupt') { send({ id: msg.id, result: {} }); return; }
    });
  `;
}

function readLines(file) {
  let content;
  try { content = fs.readFileSync(file, 'utf8'); } catch { return []; }
  return content.split('\n').filter(Boolean);
}

// `prepare(dir)` runs before the daemon starts, e.g. to steer a fixture
// Connector through files under `<dir>/data`.
export async function startMockEnv({
  codex = {},
  ovCheck = async () => {},
  config: configOverrides = {},
  prepare = null,
} = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-mock-'));
  const binDir = path.join(dir, 'bin');
  fs.mkdirSync(binDir);
  const fakeLark = path.join(binDir, 'fake-lark-cli.mjs');
  const fakeCodex = path.join(binDir, 'fake-codex.mjs');
  writeExecutable(fakeLark, fakeLarkSource(dir));
  writeExecutable(fakeCodex, fakeCodexSource(dir, {
    behavior: codex.behavior ?? 'complete',
    failBlob: codex.failBlob ?? '',
  }));
  fs.writeFileSync(path.join(dir, 'events.ndjson'), '');

  const openVikingConfigPath = path.join(dir, 'ov-config.json');
  fs.writeFileSync(openVikingConfigPath, JSON.stringify({ user: 'e2e-user' }));

  const config = {
    ...getConfig(),
    projectDir,
    dataDir: path.join(dir, 'data'),
    databasePath: path.join(dir, 'data', 'state.sqlite3'),
    lockPath: path.join(dir, 'data', 'daemon.lock'),
    // getConfig() derives the socket from the live dataDir; binding it would
    // unlink a running daemon's socket.
    actionSocketPath: path.join(dir, 'data', 'actions.sock'),
    tempDir: path.join(dir, 'data', 'tmp'),
    workspace: path.join(dir, 'workspace'),
    larkCliBin: fakeLark,
    codexBin: fakeCodex,
    profile: 'test-profile',
    initialModel: '',
    claudeModel: '',
    openVikingConfigPath,
    ignoredSenderIds: new Set(),
    port: 0,
    httpEnabled: false,
    dryRun: false,
    logLevel: 'error',
    ...configOverrides,
  };
  const daemon = new PersonalAgentDaemon(config, createLogger('error', { test: 'mock-env' }), {
    ovClient: {
      check: ovCheck,
      messages: new Map(),
      async create(sessionId) { return { session_id: sessionId, uri: `viking://session/${sessionId}` }; },
      async append(id, message) { const rows = this.messages.get(id) || []; rows.push({ ...message, parts: [{ type: 'text', text: message.content }] }); this.messages.set(id, rows); },
      async context(id) { return { latest_archive_overview: '', messages: this.messages.get(id) || [], stats: { totalArchives: 0 } }; },
      async request() { return { pending_tokens: 0 }; },
    },
  });

  const env = {
    dir,
    config,
    daemon,

    // Push one raw Lark event into the live subscription.
    receive(event) {
      fs.appendFileSync(path.join(dir, 'events.ndjson'), `${JSON.stringify(event)}\n`);
    },

    // Everything the fake codex was asked to think about: [{ method, text }].
    codexInput() {
      return readLines(path.join(dir, 'codex-input.log')).map((line) => JSON.parse(line));
    },

    // argv of every fake codex spawn (policy assertions live here:
    // developer_instructions is a `-c` config override).
    codexSpawnArgs() {
      return readLines(path.join(dir, 'codex-spawns.ndjson')).map((line) => JSON.parse(line));
    },

    // argv of every outbound lark-cli reply/send call.
    replies() {
      return readLines(path.join(dir, 'replies.ndjson')).map((line) => JSON.parse(line));
    },

    // argv of every lark-cli call.
    larkCalls() {
      return readLines(path.join(dir, 'lark-calls.ndjson')).map((line) => JSON.parse(line));
    },

    // Drop the live Lark subscription, as a lost long connection would.
    killLarkSubscriber() {
      process.kill(Number(fs.readFileSync(path.join(dir, 'subscriber.pid'), 'utf8')), 'SIGKILL');
    },

    // [{ summary, metadata }] of every activity of one kind, oldest first.
    activities(kind) {
      return daemon.store.db.prepare('SELECT summary, detail_json FROM activities WHERE kind = ? ORDER BY id')
        .all(kind).map((row) => ({ summary: row.summary, metadata: JSON.parse(row.detail_json || '{}') }));
    },

    async waitFor(predicate, timeoutMs = 8_000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        if (predicate()) return;
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
      throw new Error('mock env timed out waiting for condition');
    },

    waitForCodexInput(pattern, timeoutMs) {
      return this.waitFor(
        () => this.codexInput().some((entry) => pattern.test(entry.text)),
        timeoutMs,
      );
    },

    waitForReply(pattern, timeoutMs) {
      return this.waitFor(
        () => this.replies().some((args) => args.some((argument) => pattern.test(argument))),
        timeoutMs,
      );
    },

    async stop() {
      try {
        await daemon.shutdown('mock-env-stop', 0);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    },
  };

  try {
    prepare?.(dir);
    await daemon.start();
  } catch (error) {
    await env.stop().catch(() => {});
    throw error;
  }
  return env;
}
