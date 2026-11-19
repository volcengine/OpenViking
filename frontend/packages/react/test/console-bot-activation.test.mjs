import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { settle } from './helpers/module-dom.mjs';
import {
  deferred,
  mountActivation,
} from './helpers/console-bot-activation.mjs';

test('activation is opt-in and a command-only Console does not check or start it', async () => {
  let reads = 0;
  let statusReads = 0;
  let starts = 0;
  const activation = {
    async isEligible() {
      reads++;
      return false;
    },
    async getStatus() {
      statusReads++;
      return 'ready';
    },
    async start() {
      starts++;
    },
  };
  const host = await mountActivation({ enabled: true });
  try {
    assert.equal(host.lifecycle.state.status, 'ready');
    await host.render({ enabled: false, activation });
    assert.equal(host.lifecycle.state.status, 'disabled');
    await act(async () => host.lifecycle.start());
    assert.deepEqual([reads, statusReads, starts], [0, 0, 0]);
    await host.render({ enabled: true, activation });
    assert.equal(host.lifecycle.state.status, 'disabled');
    await act(async () => host.lifecycle.start());
    assert.deepEqual([reads, statusReads, starts], [1, 0, 0]);
    await host.render({
      enabled: true,
      activation,
      scopeKey: 'another-ineligible-scope',
    });
    assert.equal(host.lifecycle.state.status, 'disabled');
    assert.deepEqual([reads, statusReads, starts], [2, 0, 0]);
  } finally {
    await host.unmount();
  }
});

test('user startup is exclusive and chat waits for the authoritative status refresh', async () => {
  const initial = deferred();
  const started = deferred();
  const refreshed = deferred();
  const signals = [];
  let reads = 0;
  const activation = {
    async isEligible() {
      return true;
    },
    async getStatus(signal) {
      signals.push(signal);
      return reads++ === 0 ? initial.promise : refreshed.promise;
    },
    async start(signal) {
      signals.push(signal);
      await started.promise;
    },
  };
  const host = await mountActivation({ enabled: true, activation });
  try {
    assert.equal(host.lifecycle.state.status, 'checking');
    assert.equal(signals.length, 1);
    await act(async () => initial.resolve('not-started'));
    await settle();
    assert.equal(host.lifecycle.state.status, 'not-started');
    await act(async () => {
      host.lifecycle.start();
      host.lifecycle.start();
    });
    assert.equal(host.lifecycle.state.status, 'starting');
    assert.equal(signals.length, 2);
    await act(async () => started.resolve());
    await settle();
    assert.equal(host.lifecycle.state.status, 'starting');
    assert.equal(signals.length, 3);
    assert.equal(signals[1], signals[2]);
    await act(async () => refreshed.resolve('ready'));
    await settle();
    assert.equal(host.lifecycle.state.status, 'ready');
  } finally {
    await host.unmount();
  }
});

test('scope replacement aborts startup and ignores its late completion', async () => {
  const started = deferred();
  const scope = new AbortController();
  let startSignal;
  let reads = 0;
  const activation = {
    async isEligible() {
      return true;
    },
    async getStatus() {
      return reads++ === 0 ? 'not-started' : 'ready';
    },
    async start(signal) {
      startSignal = signal;
      await started.promise;
    },
  };
  const host = await mountActivation({
    enabled: true,
    activation,
    scopeKey: 'old',
  });
  try {
    await act(async () => host.lifecycle.start());
    assert.equal(host.lifecycle.state.status, 'starting');
    assert.equal(startSignal.aborted, false);
    await host.render({
      enabled: true,
      activation,
      scopeKey: 'new',
      signal: scope.signal,
    });
    assert.equal(startSignal.aborted, true);
    assert.equal(host.lifecycle.state.status, 'ready');
    await act(async () => started.resolve());
    await settle();
    assert.equal(host.lifecycle.state.status, 'ready');
    assert.equal(reads, 2);
    await act(async () => scope.abort());
    assert.equal(host.lifecycle.state.status, 'disabled');
  } finally {
    await host.unmount();
  }
});
