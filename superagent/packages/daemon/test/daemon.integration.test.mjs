// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { chatEvent } from './event-fixture.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { createLogger } from '../src/logger.mjs';
import { PersonalAgentDaemon } from '../src/daemon.mjs';
import { Harness } from '../src/harness.mjs';
import { docComment, startMockEnv, textMessage } from './mock-env.mjs';
import { OVConnection, OVUnavailableError } from '../src/ov-connection.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

describe('daemon event path (mock env)', { concurrency: false }, () => {
  it('loads OV timer state using the configured instance and persists MCP changes', async (context) => {
    const server = await fakeOV(context);
    const client = new OVConnection({ url: server.url, user: 'alice' });
    const uri = 'viking://user/alice/resources/superagent/work/timers-state.json';
    server.state.files.set(uri, JSON.stringify({
      timers: [{
        timerId: 'tm_restored', title: 'Restored', text: 'Check PR', timezone: 'UTC',
        schedule: { type: 'once' }, nextRunAtMs: Date.now() + 3_600_000,
        maxRuns: 1, delivery: { mode: 'owner' }, createdAtMs: 1,
        createdFromSource: 'lark', createdFromEventId: 'evt-restored',
        lastRunAtMs: null, runCount: 0,
      }],
      timer_occurrence_checks: [],
    }));
    const env = await startMockEnv({
      config: { timerStorage: 'ov', instanceId: 'work' },
      ovClient: { user: 'alice', json: client.json.bind(client) },
    });
    try {
      const authority = env.daemon.store.getCognitionState().authorityToken;
      const listed = await env.daemon.actions.call('list_timers', {}, { authority });
      assert.equal(listed.data.timers[0].timer_id, 'tm_restored');
      await env.daemon.actions.call('cancel_timer', { timer_id: 'tm_restored' }, { authority });
      assert.deepEqual(JSON.parse(server.state.files.get(uri)).timers, []);
      env.daemon.store.ingest(chatEvent({ eventId: 'evt-ov-create' }));
      const created = await env.daemon.actions.call('create_timer', {
        decision_id: 'evt-ov-create', title: 'New reminder', text: 'Check PR',
        schedule: { type: 'once', run_at_ms: Date.now() + 3_600_000 },
        delivery: { mode: 'owner' },
      }, { authority });
      assert.equal(JSON.parse(server.state.files.get(uri)).timers[0].timerId, created.data.timer_id);
      await env.daemon.timers.createOccurrenceCheck({
        occurrenceId: 'tm_restored:1', eventId: 'timer:tm_restored:1', nextCheckAtMs: Date.now() + 300_000,
        event: { data: { timer: { occurrence_id: 'tm_restored:1' } } },
      });
      await env.daemon.actions.call('finish_timer_occurrence', {
        occurrence_id: 'tm_restored:1', reason: 'No delivery needed',
      }, { authority });
      assert.equal(JSON.parse(server.state.files.get(uri)).timer_occurrence_checks[0].status, 'completed');
    } finally { await env.stop(); }
  });
  it('starts without history when OV is not reachable yet, and says so in the activity', async () => {
    const env = await startMockEnv({ ovCheck: async () => { throw new OVUnavailableError('OpenViking request failed or timed out'); } });
    try {
      assert.equal(env.daemon.ready, true);
      const [degraded] = env.daemon.store.listActivities().activities.filter(({ kind }) => kind === 'system.ov_unavailable');
      assert.equal(degraded.status, 'DEGRADED');
      env.receive(textMessage({ messageId: 'om-ov-down', text: 'hello' }));
      await env.waitForCodexInput(/om-ov-down/);
    } finally { await env.stop(); }
  });

  it('keeps messaging during an OV timer outage and restores timers before accepting operations', async (context) => {
    const server = await fakeOV(context);
    const client = new OVConnection({ url: server.url, user: 'alice' });
    const uri = 'viking://user/alice/resources/superagent/work/timers-state.json';
    const saved = JSON.stringify({
      timers: [{
        timerId: 'tm_restored', title: 'Restored', text: 'Check PR', timezone: 'UTC',
        schedule: { type: 'once' }, nextRunAtMs: Date.now() + 3_600_000,
        maxRuns: 1, delivery: { mode: 'owner' }, createdAtMs: 1,
        createdFromSource: 'lark', createdFromEventId: 'evt-restored',
        lastRunAtMs: null, runCount: 0,
      }],
      timer_occurrence_checks: [],
    });
    server.state.files.set(uri, saved);
    server.state.down = true;
    const env = await startMockEnv({
      config: { timerStorage: 'ov', instanceId: 'work' },
      ovCheck: async () => { throw new OVUnavailableError('HTTP 503', 503); },
      ovClient: { user: 'alice', json: client.json.bind(client) },
    });
    try {
      assert.equal(env.daemon.ready, true);
      assert.equal(env.daemon.timers.needsRefresh, true);
      assert.notEqual(env.daemon.timerConnector.timer, null);
      const authority = env.daemon.store.getCognitionState().authorityToken;
      for (const [tool, input] of [
        ['list_timers', {}],
        ['create_timer', { decision_id: 'evt-unavailable', title: 'New', text: 'New', schedule: { type: 'once', run_at_ms: Date.now() + 3_600_000 }, delivery: { mode: 'owner' } }],
        ['cancel_timer', { timer_id: 'tm_restored' }],
        ['finish_timer_occurrence', { occurrence_id: 'tm_restored:1', reason: 'No delivery needed' }],
      ]) {
        await assert.rejects(env.daemon.actions.call(tool, input, { authority }),
          (error) => error.code === 'timer_unavailable' && /temporarily unavailable/i.test(error.message));
      }
      env.receive(textMessage({ messageId: 'om-timers-down', text: 'hello' }));
      await env.waitForCodexInput(/om-timers-down/);
      await env.daemon.agentRuntime.whenIdle();
      assert.equal(server.state.files.get(uri), saved);
      assert.equal(server.state.requests.filter((request) => request.route === 'POST /api/v1/content/write').length, 0);
      server.state.down = false;
      await env.daemon.timerConnector.tick();
      assert.equal(env.daemon.timers.needsRefresh, false);
      const listed = await env.daemon.actions.call('list_timers', {}, { authority });
      assert.equal(listed.data.timers[0].timer_id, 'tm_restored');
      await env.daemon.actions.call('cancel_timer', { timer_id: 'tm_restored' }, { authority });
      assert.deepEqual(JSON.parse(server.state.files.get(uri)).timers, []);
    } finally { await env.stop(); }
  });

  it('refuses to start when OV rejects the instance\'s credentials, as no restart fixes that', async () => {
    await assert.rejects(startMockEnv({ ovCheck: async () => { throw new OVUnavailableError('OpenViking request failed (HTTP 401)', 401); } }),
      /HTTP 401/);
  });

  it('continues delivering after an OV outage and runtime rotation, with no stale history or failure notice', async () => {
    const env = await startMockEnv();
    try {
      env.receive(textMessage({ messageId: 'om-before-outage', text: 'old context marker' }));
      await env.waitForCodexInput(/om-before-outage/);
      await env.daemon.agentRuntime.whenIdle();
      const context = env.daemon.ovContext.client.context;
      env.daemon.ovContext.client.context = async () => { throw new OVUnavailableError('offline'); };
      await env.daemon.agentRuntime.transition({ kind: 'new-session' });
      env.receive(textMessage({ messageId: 'om-during-outage', text: 'new question marker' }));
      await env.waitForCodexInput(/om-during-outage/);
      const input = env.codexInput().find((entry) => entry.text.includes('om-during-outage'));
      assert.doesNotMatch(input.text, /old context marker|daemon OpenViking context/);
      assert.equal(env.daemon.ready, true);
      assert.deepEqual(env.replies(), []);
      env.daemon.ovContext.client.context = context;
      await env.daemon.ovContext.checkpoint(env.daemon.store.getCognitionState().epoch);
    } finally { await env.stop(); }
  });

  it('persists a raw event and steers the formatted line into the codex app-server', async () => {
    const env = await startMockEnv();
    try {
      env.receive(textMessage({ messageId: 'om-e2e', text: '请回复我' }));
      await env.waitForCodexInput(/om-e2e/);

      const delivered = env.codexInput();
      assert.equal(delivered.length, 1);
      assert.match(delivered[0].text, /\[chat=oc-e2e source=lark msg=om-e2e event=evt-om-e2e time=.* from=ou-e2e\] 请回复我/);

      assert.equal(env.daemon.store.stats().inbound.DONE, 1);
      // No outbox rows are ever created in steer mode.
      assert.equal(env.daemon.store.stats().outbox.PENDING, undefined);
      assert.equal(env.daemon.store.stats().outbox.SENT, undefined);

      const global = env.daemon.store.getGlobalSession();
      assert.equal(global.sessionId, 'thread-mock');
      assert.match(global.policyHash, /^[a-f0-9]{64}$/);
    } finally {
      await env.stop();
    }
  });

  it('expands a mention placeholder to the visible target name', async () => {
    const env = await startMockEnv();
    try {
      env.receive(textMessage({
        messageId: 'om-other-bot',
        chatId: 'oc-group',
        chatType: 'group',
        text: '@_user_1 飞书CLI',
        mentions: [{
          key: '@_user_1',
          name: '李雷的测试机器人',
          id: { open_id: 'ou_testbot1234567' },
        }],
      }));
      await env.waitForCodexInput(/om-other-bot/);

      const delivered = env.codexInput().find((entry) => /om-other-bot/.test(entry.text));
      assert.match(delivered.text, / mention=other\] @李雷的测试机器人 飞书CLI/);
      assert.doesNotMatch(delivered.text, /@_user_1/);
      assert.doesNotMatch(delivered.text, /\[mentions:/);
    } finally {
      await env.stop();
    }
  });

  it('tells a mention of the bot from one of a same-named bot by open_id', async () => {
    const env = await startMockEnv();
    try {
      // The Lark bot is TestBot, which is not the agent's configured name.
      const mention = (openId) => ({ key: '@_user_1', name: 'TestBot', id: { open_id: openId } });
      const send = async (messageId, mentions) => {
        env.receive(textMessage({ messageId, chatId: 'oc-group', chatType: 'group', text: '@_user_1 帮看看', mentions }));
        await env.waitForCodexInput(new RegExp(messageId));
        await env.daemon.agentRuntime.whenIdle();
        return env.codexInput().find((entry) => entry.text.includes(messageId)).text;
      };
      assert.match(await send('om-to-me', [mention('ou_mockSelf123456')]), / mention=me\] @TestBot 帮看看$/);
      assert.match(await send('om-to-twin', [mention('ou_twin1234567890')]), / mention=other\] @TestBot 帮看看$/);
      assert.match(await send('om-to-both', [mention('ou_mockSelf123456'), { key: '@_user_2', name: '韩梅梅', id: { open_id: 'ou_person12345678' } }]),
        / mention=mixed\] /);
      assert.doesNotMatch(await send('om-to-none', null), /mention=/);
    } finally {
      await env.stop();
    }
  });

  it('deduplicates a redelivered event instead of waking the model twice', async () => {
    const env = await startMockEnv();
    try {
      const message = textMessage({ messageId: 'om-dup', text: '只处理一次' });
      env.receive(message);
      await env.waitForCodexInput(/om-dup/);
      env.receive(message);
      // A later distinct message proves the duplicate was already ignored by
      // the time delivery continued.
      env.receive(textMessage({ messageId: 'om-after-dup', text: '后一条' }));
      await env.waitForCodexInput(/om-after-dup/);

      const deliveries = env.codexInput().filter((entry) => /om-dup/.test(entry.text));
      assert.equal(deliveries.length, 1);
      assert.equal(env.daemon.store.stats().inbound.DONE, 2);
    } finally {
      await env.stop();
    }
  });

  it('the Lark Connector of the instance that stores meeting notes scans for them as the owner, and nothing reaches the agent', async () => {
    const env = await startMockEnv({
      config: { meetingNotes: true },
      prepare: (dir) => fs.writeFileSync(path.join(dir, 'ov-config.json'), JSON.stringify({ url: 'http://127.0.0.1:9', user: 'e2e-user' })),
    });
    try {
      const searching = (args) => args.includes('+search') && args.includes('vc') && args.includes('user');
      await env.waitFor(() => env.larkCalls().some(searching));
      await env.waitFor(() => fs.existsSync(path.join(env.config.dataDir, 'meeting-notes', 'meetings.sqlite3')));
      const listening = env.larkCalls().find((args) => args.includes('+subscribe') && !args.includes('--help'));
      assert.doesNotMatch(listening[listening.indexOf('--event-types') + 1], /vc\.note|vc\.meeting\./, 'no meeting event is asked for');
      assert.ok(!env.larkCalls().some((args) => args.some((arg) => String(arg).includes('subscription'))), 'nothing is subscribed');
      env.receive(textMessage({ messageId: 'om-after-scan', text: 'after the scan' }));
      await env.waitForCodexInput(/om-after-scan/);
      assert.ok(!env.codexInput().some(({ text }) => /meeting/.test(text)), 'the agent hears nothing of it');
    } finally { await env.stop(); }
  });

  it('batches an idle group burst into a single model turn', async () => {
    const env = await startMockEnv();
    try {
      env.receive(textMessage({
        messageId: 'om-burst-1', chatId: 'oc-group', chatType: 'group', text: '第一条',
      }));
      env.receive(textMessage({
        messageId: 'om-burst-2', chatId: 'oc-group', chatType: 'group', text: '第二条',
      }));
      await env.waitForCodexInput(/om-burst-2/);

      const bursts = env.codexInput().filter((entry) => /om-burst/.test(entry.text));
      assert.equal(bursts.length, 1);
      assert.match(bursts[0].text, /om-burst-1[\s\S]*om-burst-2/);
    } finally {
      await env.stop();
    }
  });

  it('replies a failure notice to the sender when a codex turn fails', async () => {
    const env = await startMockEnv({
      codex: {
        behavior: 'fail',
        failBlob: JSON.stringify({
          type: 'error',
          error: { type: 'invalid_request_error', message: "Unsupported value: 'minimal' is not supported." },
          status: 400,
        }),
      },
    });
    try {
      env.receive(textMessage({ messageId: 'om-fail', text: 'hi' }));
      await env.waitForReply(/处理失败/);

      const reply = env.replies().at(-1).join(' ');
      assert.match(reply, /om-fail/);                       // replies to the failed message
      assert.match(reply, /处理失败/);
      assert.match(reply, /Unsupported value/);             // unwrapped API error, not [object Object]
      assert.doesNotMatch(reply, /object Object/);
    } finally {
      await env.stop();
    }
  });

  it('answers a slash command through the outbox without waking codex', async () => {
    const env = await startMockEnv();
    try {
      env.receive(textMessage({ messageId: 'om-cmd', text: '/h' }));
      await env.waitForReply(/可用命令/);

      const reply = env.replies().at(-1).join(' ');
      assert.match(reply, /om-cmd/);          // quote-replies the command message
      assert.match(reply, /可用命令/);         // help text body
      assert.match(reply, /thread-mock/);      // reports the current session id
      assert.equal(env.codexInput().length, 0); // codex never woke up
      assert.equal(env.daemon.store.stats().inbound.RULE_IGNORED, 1);
      // The daemon's own reply goes through the same outbox as agent deliveries.
      assert.match(reply, /--idempotency-key ld_[a-f0-9]{40}/);
      await env.waitFor(() => env.daemon.store.stats().outbox.SENT === 1);
      assert.deepEqual(env.daemon.store.stats().outbox, { SENT: 1 });
      const journal = env.daemon.store.cognitionJournal().filter((item) => item.kind === 'outbound');
      assert.deepEqual(journal.map((item) => [item.status, item.messageId]), [['COMMITTED', 'om-mock-out']]);
    } finally {
      await env.stop();
    }
  });

  it('records an explicit stay_silent from the runtime, with no reply and no reminder turn', async () => {
    const env = await startMockEnv({ codex: { behavior: 'silent' }, config: { explicitSilence: true } });
    try {
      env.receive(textMessage({ messageId: 'om-silent', text: '谢谢' }));
      await env.waitFor(() => env.daemon.store.db.prepare('SELECT COUNT(*) AS n FROM decisions').get().n === 1);
      await env.daemon.agentRuntime.whenIdle();
      const [decision] = env.daemon.store.db.prepare('SELECT event_id, outcome FROM decisions').all();
      assert.deepEqual({ ...decision }, { event_id: 'evt-om-silent', outcome: 'silent' });
      assert.deepEqual(env.activities('delivery.silent').map((row) => row.summary), ['mock']);
      // Give a reminder turn the chance to appear: none does.
      await new Promise((resolve) => setTimeout(resolve, 300));
      assert.deepEqual(env.codexInput().map((entry) => entry.method), ['turn/start']);
      assert.deepEqual(env.replies(), []);
      assert.deepEqual(env.activities('agent.decision_missing'), []);
    } finally {
      await env.stop();
    }
  });

  it('reminds once about a direct message the turn did not decide on', async () => {
    const env = await startMockEnv({ codex: { behavior: 'complete' }, config: { explicitSilence: true } });
    try {
      env.receive(textMessage({ messageId: 'om-undecided', text: '在吗' }));
      await env.waitFor(() => env.codexInput().length === 2);
      await env.daemon.agentRuntime.whenIdle();
      const turns = env.codexInput();
      assert.doesNotMatch(turns[0].text, /undecided=1/);
      assert.match(turns[1].text, /msg=om-undecided event=evt-om-undecided [^\]]* undecided=1\] 在吗/);
      assert.deepEqual(env.daemon.store.db.prepare("SELECT status FROM activities WHERE kind = 'agent.decision_missing' ORDER BY id")
        .all().map((row) => row.status), ['NUDGED']);
      await new Promise((resolve) => setTimeout(resolve, 300));
      assert.equal(env.codexInput().length, 2, 'never a second reminder');
      assert.deepEqual(env.replies(), []);
    } finally {
      await env.stop();
    }
  });

  it('reminds once about an ordinary group message the turn did not decide on', async () => {
    const env = await startMockEnv({ codex: { behavior: 'complete' }, config: { explicitSilence: true } });
    try {
      env.receive(textMessage({ messageId: 'om-group-undecided', chatId: 'oc-group', chatType: 'group', text: '大家早' }));
      await env.waitFor(() => env.codexInput().length === 2);
      const turns = env.codexInput();
      assert.match(turns[1].text, /chat=oc-group [^\]]*msg=om-group-undecided [^\]]* undecided=1\] 大家早/);
      assert.deepEqual(env.replies(), []);
    } finally {
      await env.stop();
    }
  });

  it('stops a turn that keeps redoing a settled decision and goes on in the same session with what arrived meanwhile', async () => {
    const env = await startMockEnv({ codex: { behavior: 'loop' }, config: { explicitSilence: true } });
    const decided = () => env.daemon.store.db.prepare('SELECT event_id, outcome FROM decisions ORDER BY event_id').all()
      .map((row) => `${row.event_id}:${row.outcome}`);
    try {
      env.receive(textMessage({ messageId: 'om-loop', chatId: 'oc-group', chatType: 'group', text: 'LOOP 群里闲聊' }));
      await env.waitFor(() => decided().length === 1);
      const epoch = env.daemon.store.getCognitionState().epoch;
      // Arrives while the model repeats itself: steered into the stuck turn.
      env.receive(textMessage({ messageId: 'om-waiting', text: '在吗' }));
      await env.waitFor(() => env.activities('agent.loop_stopped').length === 1);
      await env.waitFor(() => decided().length === 2);
      await env.daemon.agentRuntime.whenIdle();

      assert.deepEqual(decided(), ['evt-om-loop:silent', 'evt-om-waiting:silent']);
      assert.deepEqual(env.codexInput().map((entry) => entry.method),
        ['turn/start', 'turn/steer', 'turn/interrupt', 'turn/start']);
      const [stop] = env.activities('agent.loop_stopped');
      assert.equal(stop.metadata.action, 'cancel');
      assert.equal(stop.metadata.tool, 'stay_silent');
      assert.equal(stop.metadata.count, 3);
      // Same process, same thread, same cognition: only the turn ended.
      assert.equal(env.codexSpawnArgs().length, 1);
      assert.equal(env.daemon.agentRuntime.threadId, 'thread-mock');
      assert.equal(env.daemon.store.getCognitionState().epoch, epoch);
      // The next turn is told why, and gets the steered line back undecided.
      const next = env.codexInput().at(-1).text;
      assert.match(next, /^\[daemon notice\] The daemon stopped your previous turn: it called stay_silent for event=evt-om-loop 3 times/);
      assert.match(next, /msg=om-waiting event=evt-om-waiting [^\]]* undecided=1\] 在吗/);
      // Ended on purpose: no failure notice, nothing sent to anyone.
      assert.deepEqual(env.activities('agent.failure_notice'), []);
      assert.deepEqual(env.replies(), []);
      await new Promise((resolve) => setTimeout(resolve, 200));
      assert.equal(env.codexInput().length, 4, 'the stopped loop does not come back');
    } finally {
      await env.stop();
    }
  });

  it('moves a session stopped twice within the window to a new one that keeps the history', async () => {
    const env = await startMockEnv({ codex: { behavior: 'loop' }, config: { explicitSilence: true } });
    try {
      env.receive(textMessage({ messageId: 'om-loop-1', chatId: 'oc-group', chatType: 'group', text: 'LOOP 第一次' }));
      await env.waitFor(() => env.activities('agent.loop_stopped').length === 1, 6_000);
      await env.daemon.agentRuntime.whenIdle();
      const epoch = env.daemon.store.getCognitionState().epoch;
      env.receive(textMessage({ messageId: 'om-loop-2', chatId: 'oc-group', chatType: 'group', text: 'LOOP 第二次' }));
      await env.waitFor(() => env.activities('agent.loop_stopped').length === 2, 6_000);
      await env.daemon.agentRuntime.whenIdle();
      env.receive(textMessage({ messageId: 'om-after', text: '还在吗' }));
      await env.waitForCodexInput(/msg=om-after/);

      assert.deepEqual(env.activities('agent.loop_stopped').map((row) => row.metadata.action), ['cancel', 'new-session']);
      // A fresh native session in the same cognition epoch, primed with history.
      assert.equal(env.daemon.store.getCognitionState().epoch, epoch);
      const next = env.codexInput().find((entry) => /msg=om-after/.test(entry.text)).text;
      assert.match(next, /--- history above, new messages below ---\n\[daemon notice\] [^\n]*for event=evt-om-loop-2 3 times[^\n]*You are now in a new session/);
      assert.match(next, /LOOP 第二次/, 'the history carries the stopped input');
      assert.deepEqual(env.activities('agent.failure_notice'), []);
    } finally {
      await env.stop();
    }
  });

  it('gets a line steered into a stopped turn handled on an instance without explicitSilence', async () => {
    const env = await startMockEnv({ codex: { behavior: 'reply-loop' } });
    const repliesTo = (id) => env.replies().filter((args) => args.join(' ').includes(id)).length;
    try {
      env.receive(textMessage({ messageId: 'om-loop', chatId: 'oc-group', chatType: 'group', text: 'LOOP 帮我看下' }));
      await env.waitFor(() => repliesTo('om-loop') === 1);
      // Steered into the turn that then keeps resending its reply.
      env.receive(textMessage({ messageId: 'om-waiting', text: '在吗' }));
      await env.waitFor(() => env.activities('agent.loop_stopped').length === 1);
      await env.waitFor(() => repliesTo('om-waiting') === 1);

      const next = env.codexInput().filter((entry) => entry.method === 'turn/start').at(-1).text;
      assert.match(next, /^\[daemon notice\] The daemon stopped your previous turn: it called reply_message for event=evt-om-loop 3 times[^\n]*That input already has its decision/);
      assert.match(next, /msg=om-waiting event=evt-om-waiting [^\]]*\] 在吗/);
      assert.doesNotMatch(next, /undecided=1/, 'the tag means nothing without explicitSilence');
      assert.equal(repliesTo('om-loop'), 1, 'the repeated reply went out once');
      assert.deepEqual(env.activities('agent.failure_notice'), []);
    } finally {
      await env.stop();
    }
  });

  it('still stops a loop in the session that replaced a session stopped twice', async () => {
    const env = await startMockEnv({ codex: { behavior: 'loop' }, config: { explicitSilence: true } });
    try {
      env.receive(textMessage({ messageId: 'om-loop-a', chatId: 'oc-group', chatType: 'group', text: 'LOOP a' }));
      await env.waitFor(() => env.daemon.store.db.prepare('SELECT COUNT(*) AS n FROM decisions').get().n === 1);
      // Steered in, then brought back after the first stop: that turn loops too.
      env.receive(textMessage({ messageId: 'om-loop-b', text: 'LOOP b' }));
      await env.waitFor(() => env.activities('agent.loop_stopped').length === 2, 10_000);
      await env.daemon.agentRuntime.whenIdle();
      env.receive(textMessage({ messageId: 'om-loop-c', chatId: 'oc-group', chatType: 'group', text: 'LOOP c' }));
      await env.waitFor(() => env.activities('agent.loop_stopped').length === 3, 10_000);
      assert.deepEqual(env.activities('agent.loop_stopped').map((row) => row.metadata.action), ['cancel', 'new-session', 'cancel']);
    } finally {
      await env.stop();
    }
  });

  it('wakes on an @mention in a document comment and answers in that comment', async () => {
    const env = await startMockEnv({ codex: { behavior: 'reply' } });
    try {
      env.receive(docComment());
      await env.waitForReply(/^\+add-reply$/);
      const [turn] = env.codexInput();
      assert.match(turn.text, new RegExp('^\\[chat=doc:docx:doxE2eToken thread=c-e2e source=lark msg=comment:docx:doxE2eToken:c-e2e:r-e2e '
        + 'event=evt-c-e2e-r-e2e time=\\S+ from=Commenter mention=me\\] \\[document "E2E 文档" https://example.feishu.cn/docx/doxE2eToken\\] '
        + '⏎ \\[on the text: "第二季度目标"\\] ⏎ @TestBot 帮我看下这段$', 'm'));
      const reply = env.replies().find((args) => args.includes('+add-reply'));
      assert.deepEqual(reply.slice(reply.indexOf('drive')), ['drive', '+add-reply', '--as', 'bot', '--token', 'doxE2eToken', '--type', 'docx',
        '--comment-id', 'c-e2e', '--content', JSON.stringify([{ type: 'text', text: 'mock reply' }]), '--format', 'json']);
      assert.ok(!env.larkCalls().some((args) => args.includes('+chat-members-list')), 'a document has no roster to fetch');
      // lark-cli answered; the Core settles the outbox right after.
      const outbox = () => env.daemon.store.db.prepare("SELECT status, remote_message_id FROM outbox WHERE action = 'message.reply'").get();
      await env.waitFor(() => outbox()?.status === 'SENT');
      assert.equal(outbox().remote_message_id, 'comment:docx:doxE2eToken:c-e2e:r-e2e-out');
    } finally {
      await env.stop();
    }
  });

  it('without explicit silence (the default) checks no decisions and offers no stay_silent', async () => {
    const env = await startMockEnv({ codex: { behavior: 'complete' }, });
    try {
      // Kept only to recover what a loop-stopped turn left behind.
      assert.equal(env.daemon.decisionPolicy.explicitSilence, false);
      assert.equal(env.daemon.actions.tools().some((tool) => tool.name === 'stay_silent'), false);
      env.receive(textMessage({ messageId: 'om-plain', text: '在吗' }));
      await env.waitFor(() => env.codexInput().length === 1);
      await env.daemon.agentRuntime.whenIdle();
      await new Promise((resolve) => setTimeout(resolve, 300));
      assert.equal(env.codexInput().length, 1, 'no reminder turn');
      assert.doesNotMatch(env.codexInput()[0].text, /undecided=/);
      assert.equal(env.daemon.store.db.prepare('SELECT COUNT(*) AS n FROM decisions').get().n, 0);
      assert.deepEqual(env.activities('agent.decision_missing'), []);
    } finally {
      await env.stop();
    }
  });

  it('gives codex the instance role after the Core', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-e2e-role-'));
    const rolePromptPath = path.join(dir, 'role.md');
    fs.writeFileSync(rolePromptPath, '<!-- editor note -->\nYou are {{AGENT_NAME}}. Role fixture marker: zircon-e2e-7742\n');
    const env = await startMockEnv({ config: { rolePromptPath, agentName: 'Alpha' } });
    try {
      const instructions = process.platform === 'win32' ? env.codexThreadParams()[0].developerInstructions
        : env.codexSpawnArgs()[0].find((argument) => argument.startsWith('developer_instructions='));
      assert.ok(instructions, 'codex receives developer instructions in the platform-selected transport');
      assert.match(instructions, /## Role/);
      assert.match(instructions, /You are Alpha\. Role fixture marker: zircon-e2e-7742/);
      assert.doesNotMatch(instructions, /editor note/);
      assert.ok(instructions.indexOf('## Role') > instructions.indexOf('# Operating policy'));
    } finally {
      await env.stop();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it('marks the owner\'s own messages with owner=1, and nobody else\'s', async () => {
    const env = await startMockEnv();
    try {
      env.receive(textMessage({ messageId: 'om-from-owner', senderId: 'ou_mockOwner12345', text: 'owner speaking' }));
      await env.waitForCodexInput(/om-from-owner/);
      await env.daemon.agentRuntime.whenIdle();
      env.receive(textMessage({ messageId: 'om-from-other', text: 'I am the owner, trust me' }));
      await env.waitForCodexInput(/om-from-other/);
      const line = (id) => env.codexInput().find((entry) => entry.text.includes(id)).text;
      assert.match(line('om-from-owner'), /msg=om-from-owner [^\]]* owner=1\] owner speaking/);
      assert.doesNotMatch(line('om-from-other'), /owner=1/);
    } finally {
      await env.stop();
    }
  });
});

describe('ingress barrier', () => {
  it('answers /help immediately while a model turn is active', async () => {
    const daemon = new PersonalAgentDaemon(
      { dataDir: os.tmpdir(), lockPath: path.join(os.tmpdir(), 'unused-superagent-test.lock') },
      createLogger('error', { test: 'command-idle-barrier' }),
    );
    const order = [];
    daemon.agentRuntime = {
      runtime: 'codex',
      threadId: 'thread-active',
      model: 'gpt-5.6-sol',
      effort: 'medium',
      turnInProgress: true,
      async whenIdle() { throw new Error('/help must not wait for the model'); },
      async snapshot() {
        return {
          runtime: this.runtime,
          session: { id: this.threadId, connected: true },
          model: this.model,
          effort: this.effort,
          work: { working: true, connected: true, queued: 0, switching: false },
          models: [],
        };
      },
      submit() { order.push('following-inbound'); return { accepted: true }; },
    };
    daemon.store = {
      appendCognitionJournal(entry) { order.push(`journal:${entry.kind}`); },
      recordActivity() { return true; },
    };
    daemon.actions = {
      async replyToEvent() { order.push('command-reply'); return { ok: true }; },
    };
    const harness = new Harness({
      config: {},
      store: daemon.store,
      logger: createLogger('error', { test: 'command-idle-barrier' }),
      recordActivity: () => true,
      runtime: daemon.agentRuntime,
      onSlashCommand: (command, event, inbound) => daemon.handleSlashCommand(command, event, inbound),
    });
    const event = chatEvent({
      chatId: 'oc_command123456',
      threadId: null,
      messageId: 'om_command123456',
      senderId: 'ou_sender123456',
      content: '/help',
      conversationKey: 'profile:tenant:oc_command123456:main',
      chatType: 'p2p',
    });

    const command = harness.enqueueIngress(
      () => daemon.handleSlashCommand({ name: 'help', arg: '' }, event, { seq: 1 }),
    );
    const following = harness.enqueueIngress(() => daemon.agentRuntime.submit([{ text: 'next', seq: 2 }]));
    await Promise.all([command, following]);
    assert.deepEqual(order, ['journal:control', 'command-reply', 'following-inbound']);
  });
});

describe('/model immediate switch', () => {
  it('executes /model without waiting for the stuck active turn', async () => {
    const daemon = new PersonalAgentDaemon(
      { dataDir: os.tmpdir(), lockPath: path.join(os.tmpdir(), 'unused-superagent-model.lock') },
      createLogger('error', { test: 'model-no-wait' }),
    );
    const order = [];
    daemon.agentRuntime = {
      runtime: 'codex',
      threadId: 'th-stuck',
      model: '',
      effort: 'medium',
      turnInProgress: true,
      // A stuck turn: /model must never block behind this.
      whenIdle() { order.push('wait-idle'); return new Promise(() => {}); },
      async snapshot() {
        return {
          runtime: this.runtime,
          session: { id: this.threadId, connected: true },
          model: this.model,
          effort: this.effort,
          work: { working: true, connected: true, queued: 0, switching: false },
          capabilities: { effortControl: 'selectable' },
          models: [],
        };
      },
      async transition(request) {
        order.push(`setModel:${request.selector}`);
        return { ok: true, runtimeChanged: false, model: request.selector, threadId: 'th-fresh' };
      },
    };
    daemon.store = {
      appendCognitionJournal() {},
      recordActivity() { return true; },
    };
    const replies = [];
    daemon.actions = {
      async replyToEvent(event, text) { replies.push({ messageId: event.messageId, text }); return { ok: true }; },
    };
    const event = chatEvent({
      chatId: 'oc_model123456',
      threadId: null,
      messageId: 'om_model123456',
      senderId: 'ou_sender123456',
      content: '/model gpt-x',
      conversationKey: 'profile:tenant:oc_model123456:main',
      chatType: 'p2p',
    });

    await daemon.handleSlashCommand({ name: 'model', arg: 'gpt-x' }, event, { seq: 1 });
    assert.deepEqual(order, ['setModel:gpt-x']);
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /已切到 gpt-x（下一轮生效，上下文保留）/);
  });
});
