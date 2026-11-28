import assert from 'node:assert/strict';
import test from 'node:test';
import { waitForFlowCompletion } from './flow-completion.mjs';

function clock(start = 0) {
  let value = start;
  return {
    now: () => value,
    sleep: async (ms) => { value += ms; },
  };
}

test('finishes immediately when every target event has a decision', async () => {
  const time = clock();
  const logs = [];
  const result = await waitForFlowCompletion({
    eventIds: ['event-1', 'event-2'],
    readDecisionIds: () => new Set(['event-1', 'event-2']),
    readLastOutboxAtMs: () => null,
    injectedAtMs: 0,
    now: time.now,
    sleep: time.sleep,
    log: (line) => logs.push(line),
  });

  assert.deepEqual(result, { reason: 'all-decided', decided: 2, expected: 2 });
  assert.deepEqual(logs, []);
});

test('finishes after five minutes without a new outbox and logs every minute', async () => {
  const time = clock();
  const logs = [];
  const result = await waitForFlowCompletion({
    eventIds: ['event-1', 'event-2'],
    readDecisionIds: () => new Set(['event-1']),
    readLastOutboxAtMs: () => 0,
    injectedAtMs: 0,
    now: time.now,
    sleep: time.sleep,
    pollIntervalMs: 60_000,
    log: (line) => logs.push(line),
  });

  assert.deepEqual(result, { reason: 'outbox-idle', decided: 1, expected: 2, idleMs: 300_000 });
  assert.deepEqual(logs, [
    'decision wait: 1/2 decided, no new outbox for 1/5 minutes',
    'decision wait: 1/2 decided, no new outbox for 2/5 minutes',
    'decision wait: 1/2 decided, no new outbox for 3/5 minutes',
    'decision wait: 1/2 decided, no new outbox for 4/5 minutes',
    'decision wait: 1/2 decided, no new outbox for 5/5 minutes',
  ]);
});

test('a newer outbox resets both the five-minute clock and minute logs', async () => {
  const time = clock();
  const logs = [];
  const result = await waitForFlowCompletion({
    eventIds: ['event-1'],
    readDecisionIds: () => new Set(),
    readLastOutboxAtMs: () => time.now() >= 180_000 ? 180_000 : 0,
    injectedAtMs: 0,
    now: time.now,
    sleep: time.sleep,
    pollIntervalMs: 60_000,
    log: (line) => logs.push(line),
  });

  assert.equal(time.now(), 480_000);
  assert.deepEqual(result, { reason: 'outbox-idle', decided: 0, expected: 1, idleMs: 300_000 });
  assert.deepEqual(logs, [
    'decision wait: 0/1 decided, no new outbox for 1/5 minutes',
    'decision wait: 0/1 decided, no new outbox for 2/5 minutes',
    'decision wait: 0/1 decided, no new outbox for 1/5 minutes',
    'decision wait: 0/1 decided, no new outbox for 2/5 minutes',
    'decision wait: 0/1 decided, no new outbox for 3/5 minutes',
    'decision wait: 0/1 decided, no new outbox for 4/5 minutes',
    'decision wait: 0/1 decided, no new outbox for 5/5 minutes',
  ]);
});
