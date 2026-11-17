import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { startMockEnv, textMessage } from '../../packages/daemon/test/mock-env.mjs';
import { createIsolatedTraex } from './environment.mjs';

const enabled = process.env.RUN_FRESHNESS_CHAIN === '1';
const messages = [
  '1+1 等于多少？请回答这一题。',
  '法国的首都是哪里？请回答这一题。',
  '水的化学式是什么？请回答这一题。',
  '一周有几天？请回答这一题。',
  '地球有几颗天然卫星？请回答这一题。',
];
const eventId = (index) => `freshness-chain-${index + 1}`;
const messageId = (index) => `om_freshness_chain_${index + 1}`;

test('five-message freshness chain dataset is fixed', () => {
  assert.equal(messages.length, 5);
});

test('four successive replies are stale and the fifth settles all five inputs', {
  skip: !enabled,
  timeout: 5 * 60_000,
}, async () => {
  const output = path.resolve(process.env.FRESHNESS_CHAIN_OUTPUT || '/tmp/message-decision-e2e-freshness-chain');
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  const isolated = createIsolatedTraex(path.join(output, 'runtime'), {
    authPath: process.env.FRESHNESS_CHAIN_AUTH || path.join(process.env.HOME, '.trae/cli/auth.json'),
    binary: process.env.FRESHNESS_CHAIN_BIN || '/Users/bytedance/.local/bin/traecli',
  });
  let env;
  let next = 1;
  const inject = (index) => env.receive(textMessage({
    messageId: messageId(index),
    eventId: eventId(index),
    chatId: 'oc_freshness_chain',
    chatType: 'group',
    threadId: 'omt_freshness_chain',
    senderId: 'ou_freshness_chain',
    text: messages[index],
  }));
  env = await startMockEnv({
    config: {
      explicitSilence: true,
      naturalWriting: false,
      agentName: '机器人',
      decisionReminderDelayMs: 1_000,
      decisionReminderLimit: 1,
      initialModel: `traex:${process.env.FRESHNESS_CHAIN_MODEL || 'GPT-5.6-Sol'}`,
      traexBin: isolated.bin,
      beforeFinalFreshnessCheck: async ({ action, decision }) => {
        if (action !== 'message.reply' || !decision || next >= messages.length) return;
        const index = next;
        next += 1;
        inject(index);
        await env.waitFor(() => env.daemon.store.db.prepare(
          'SELECT COUNT(*) AS count FROM connector_events WHERE event_id = ? AND status = ?',
        ).get(eventId(index), 'DONE').count === 1);
      },
    },
    codex: { behavior: 'complete' },
  });

  try {
    inject(0);
    await env.waitFor(() => env.daemon.store.db.prepare(
      'SELECT COUNT(*) AS count FROM decisions WHERE event_id LIKE ?',
    ).get('freshness-chain-%').count === messages.length, 4 * 60_000);
    await env.waitFor(() => env.daemon.store.db.prepare(
      "SELECT COUNT(*) AS count FROM outbox WHERE status = 'PENDING'",
    ).get().count === 0, 10_000);

    const decisions = env.daemon.store.db.prepare(
      'SELECT event_id, outcome, covered_by FROM decisions WHERE event_id LIKE ? ORDER BY seq',
    ).all('freshness-chain-%');
    const stale = env.daemon.store.db.prepare(`
      SELECT e.event_id
      FROM activities a JOIN connector_events e ON e.seq = a.inbound_seq
      WHERE a.kind = 'delivery.blocked'
        AND json_extract(a.detail_json, '$.code') = 'stale_decision'
        AND e.event_id LIKE 'freshness-chain-%'
      ORDER BY e.seq
    `).all().map(({ event_id: id }) => id);
    const replies = env.daemon.store.db.prepare(
      "SELECT status FROM outbox WHERE action = 'message.reply' ORDER BY id",
    ).all();
    const result = { decisions, stale, replies };
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));

    assert.deepEqual(stale, [0, 1, 2, 3].map(eventId));
    assert.deepEqual(decisions.map(({ outcome }) => outcome), Array(5).fill('delivered'));
    assert.equal(decisions.some(({ outcome }) => outcome === 'missing'), false);
    assert.equal(replies.filter(({ status }) => status === 'SENT').length, 1);
  } finally {
    await env.stop();
  }
});
