// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';

import { pidAlive, watchdogMain } from '../src/restart-watchdog.mjs';

function fixture(overrides = {}) {
  const events = [];
  let alive = true;
  const state = {
    events,
    starts: [],
    notes: [],
    alive: () => alive,
    kill: () => { alive = false; },
    // The restart command dies on the first poll, like one killed with the tree.
    wait: async (ms) => { events.push(`wait ${ms}`); alive = false; },
  };
  state.options = {
    instance: 'alpha',
    waitPid: 4242,
    settleMs: 0,
    alive: state.alive,
    wait: state.wait,
    note: (message) => { state.notes.push(message); },
    start: async (instance, options) => { state.starts.push({ instance, options }); return 0; },
    ...overrides,
  };
  return state;
}

test('waits for the restart command to disappear, then registers the app', async () => {
  const f = fixture();
  const code = await watchdogMain(f.options);
  assert.equal(code, 0);
  assert.deepEqual(f.starts.map(({ instance }) => instance), ['alpha']);
  assert.equal(f.starts[0].options.env, process.env);
  assert.deepEqual(f.notes.map((line) => line.split(': ')[1]), ['restart command 4242 is gone; making sure the app is up', 'registered']);
  assert.deepEqual(f.events, ['wait 500']);
});

test('settles before starting, so pm2 has finished removing the entry', async () => {
  const f = fixture({ settleMs: 5_000 });
  await watchdogMain(f.options);
  assert.deepEqual(f.events, ['wait 500', 'wait 5000']);
});

test('registers anyway when the command it waits for never goes away', async () => {
  const f = fixture();
  let clock = 0;
  const code = await watchdogMain({
    ...f.options,
    deadlineMs: 1_000,
    pollMs: 400,
    now: () => clock,
    // Every poll advances the clock past the deadline without the pid dying.
    wait: async () => { clock += 500; },
  });
  assert.equal(code, 0);
  assert.match(f.notes[0], /still runs after 1000ms/);
  assert.equal(f.starts.length, 1);
});

test('retries once and reports the reason when the app does not come up', async () => {
  const f = fixture();
  let attempt = 0;
  const code = await watchdogMain({
    ...f.options,
    start: async (instance, options) => {
      attempt += 1;
      options.stderr.write('alpha: daemon did not become ready: 503\n');
      return 1;
    },
  });
  assert.equal(code, 1);
  assert.equal(attempt, 2);
  assert.match(f.notes.join('\n'), /start attempt 1 failed: alpha: daemon did not become ready: 503/);
  assert.match(f.notes.join('\n'), /start attempt 2 failed/);
});

test('a start that throws counts as a failed attempt, not a crash', async () => {
  const f = fixture();
  const code = await watchdogMain({
    ...f.options,
    start: async () => { throw new Error('no release installed'); },
  });
  assert.equal(code, 1);
  assert.match(f.notes.join('\n'), /no release installed/);
});

test('does nothing without an instance', async () => {
  const f = fixture({ instance: undefined });
  assert.equal(await watchdogMain(f.options), 2);
  assert.deepEqual(f.starts, []);
});

test('pidAlive tells a live process from a gone one', () => {
  assert.equal(pidAlive(process.pid), true);
  assert.equal(pidAlive(2147483647), false);
  for (const bad of [undefined, null, 0, -1, 'abc', 1.5]) assert.equal(pidAlive(bad), false);
});
