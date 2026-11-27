// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';
import { ConnectorManager } from '../src/connectors/process.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { startMockEnv, textMessage } from './mock-env.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const fixture = fileURLToPath(new URL('./connector-worker-fixture.mjs', import.meta.url));
const mixedFixture = fileURLToPath(new URL('./connector-worker-mixed-fixture.mjs', import.meta.url));

async function waitFor(predicate, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('timed out waiting for condition');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function lineCount(file) {
  try { return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).length; } catch { return 0; }
}

// Two fixture Connectors, alpha and beta, each steered by a mode file. A
// session-end exit counts from the first second on unless a test asks for the
// uptime window the daemon uses.
function setup(t, { modes = {}, delays = [50, 100], windowMs = 0 } = {}) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'connector-supervision-'));
  const setMode = (id, mode) => fs.writeFileSync(path.join(dataDir, `${id}.mode`), mode);
  for (const [id, mode] of Object.entries(modes)) setMode(id, mode);
  const registry = new ProcessRegistry(logger);
  const manager = new ConnectorManager({
    connectors: ['alpha', 'beta'], dataDir, projectDir: process.cwd(), maxEventBytes: 100_000,
    connectorWorkerPath: fixture, connectorRestartDelaysMs: delays, connectorStableAfterMs: 100,
    connectorSessionEndWindowMs: windowMs,
  }, registry, logger);
  const events = [];
  const statuses = [];
  t.after(async () => {
    await manager.stop();
    await registry.stopAll(1_000);
    fs.rmSync(dataDir, { recursive: true, force: true });
  });
  return {
    manager, events, statuses, setMode,
    spawns: (id) => lineCount(path.join(dataDir, `${id}.spawns`)),
    // onStatus runs inside the report, before any restart can fire.
    start: (onStatus = () => {}) => manager.startSubscription(
      (event) => events.push(`${event.connector}:${event.id}`),
      (status) => {
        statuses.push({ ...status, snapshot: manager.snapshot() });
        onStatus(status);
      },
    ),
  };
}

describe('Connector supervision', () => {
  it('restarts only the Connector that exited while the others keep running', async (t) => {
    const { manager, events, statuses, setMode, spawns, start } = setup(t, { modes: { beta: 'exit-after-start' } });
    await manager.preflight();
    // The login is renewed while beta waits for its restart.
    await start((status) => { if (status.status === 'failed') setMode('beta', 'ok'); });
    const alpha = manager.connector('alpha').child.pid;

    await waitFor(() => statuses.some((status) => status.status === 'recovered'));
    const [failed, recovered] = statuses;
    assert.equal(failed.status, 'failed');
    assert.equal(failed.connector, 'beta');
    assert.equal(failed.code, 1);
    assert.equal(failed.retryInMs, 50);
    assert.match(failed.error, /session expired; run the QR login command again/);
    assert.equal(failed.snapshot.alpha.state, 'running');
    assert.equal(failed.snapshot.beta.state, 'restarting');
    assert.match(failed.snapshot.beta.error, /session expired/);
    assert.deepEqual([recovered.status, recovered.connector, recovered.failures], ['recovered', 'beta', 1]);
    assert.equal(statuses.length, 2);

    assert.equal(manager.connector('alpha').child.pid, alpha, 'alpha was never restarted');
    assert.equal(spawns('alpha'), 1);
    assert.equal(spawns('beta'), 2);
    assert.equal(manager.subscriptionRunning, true);
    assert.equal(manager.available('beta'), true);
    await waitFor(() => events.includes('beta:issue-open-123-2'));
    assert.deepEqual(events.filter((event) => event.startsWith('alpha:')), ['alpha:issue-open-123']);
  });

  it('starts with the Connectors that pass check and retries the rest with backoff', async (t) => {
    const { manager, statuses, setMode, spawns, start } = setup(t, { modes: { beta: 'fail-check' } });
    const info = await manager.preflight();
    assert.deepEqual(Object.keys(info.connectors), ['alpha']);
    assert.match(info.failed.beta, /login expired/);
    assert.equal(info.version, 'alpha:test');

    // Nothing reaches a Connector that is down, and no worker is started for it.
    await assert.rejects(manager.invoke('beta', 'message.send', {}), { code: 'connector_unavailable' });
    assert.equal(manager.available('beta'), false);
    assert.equal(spawns('beta'), 1);

    await start((status) => { if (status.failures === 2) setMode('beta', 'ok'); });
    assert.equal(manager.anySubscriptionRunning, true);
    assert.equal(manager.subscriptionRunning, false);
    await waitFor(() => statuses.some((status) => status.status === 'recovered'));
    assert.deepEqual(statuses.map((status) => [status.status, status.retryInMs ?? null, status.failures]),
      [['failed', 50, 1], ['failed', 100, 2], ['recovered', null, 2]]);
    assert.ok(statuses.slice(0, 2).every((status) => /login expired/.test(status.error)));
    assert.equal(spawns('beta'), 3);
    assert.equal(manager.subscriptionRunning, true);
  });

  it('still fails startup when no Connector passes check', async (t) => {
    const { manager } = setup(t, { modes: { alpha: 'fail-check', beta: 'fail-check' } });
    await assert.rejects(manager.preflight(), /alpha: .*login expired.*; beta: .*login expired/);
  });

  it('gives the exit reason, not the last routine stderr line, and keeps that tail in the report', async (t) => {
    const { manager, statuses, start } = setup(t, { modes: { alpha: 'exit-code-only' }, delays: [60_000] });
    await manager.preflight();
    await start();
    await waitFor(() => statuses.length === 1);
    assert.equal(statuses[0].error, 'connector alpha exited with code 3');
    assert.equal(statuses[0].code, 1);
    // The worker's own stderr stays available for a crash, in the report alone.
    assert.match(statuses[0].stderr, /meeting_notes_scanned/);
  });

  it('schedules no restart once shutdown pauses the subscriptions, even if a worker exits first', async (t) => {
    const { manager, statuses, spawns, start } = setup(t, { modes: { beta: 'exit-after-start' }, delays: [200] });
    await manager.preflight();
    await start();
    await waitFor(() => statuses.length === 1);
    const alpha = manager.connector('alpha').child;
    await manager.pauseSubscriptions();
    // systemd signals the whole cgroup, so a worker can die before Core stops it.
    alpha.kill('SIGTERM');
    await delay(400);
    assert.equal(statuses.length, 1);
    assert.equal(spawns('beta'), 1);
    assert.deepEqual(Object.values(manager.snapshot()).map((connector) => connector.state), ['stopped', 'stopped']);
  });

  it('treats a Windows session-end exit as the session ending, not a Connector failure', async (t) => {
    const { manager, statuses, spawns, start } = setup(t, { modes: { beta: 'exit-session-end' }, delays: [50] });
    await manager.preflight();
    await start();
    await waitFor(() => statuses.length === 1);
    // The worker saw its subscriber killed with 0x40010004 and exited 1 itself:
    // the reported exit is the one that counts, and the session is over.
    assert.deepEqual(statuses.map((status) => status.status), ['session_end']);
    assert.deepEqual([statuses[0].connector, statuses[0].code, statuses[0].reportedCode], ['beta', 1, 0x40010004]);
    // Nothing is retried: no 5-second reconnect that could only fail.
    await delay(300);
    assert.equal(spawns('beta'), 1);
    assert.deepEqual(Object.values(manager.snapshot()).map((connector) => connector.state), ['running', 'stopped']);
  });

  it('reports a session-end code inside the uptime window as a failure, so a restart loop cannot hide', async (t) => {
    const { manager, statuses, spawns, start } = setup(t, {
      modes: { alpha: 'exit-session-end' }, delays: [60_000], windowMs: 30_000,
    });
    await manager.preflight();
    await start();
    await waitFor(() => statuses.length === 1);
    // The Core was started seconds ago: the same code is a broken environment,
    // not the session ending, and the supervisor must see it.
    assert.equal(statuses[0].status, 'failed');
    assert.match(statuses[0].error, /with code 1073807364 \(DBG_TERMINATE_PROCESS\)/);
    assert.equal(spawns('alpha'), 1);
  });

  it('treats a session end that lands while a Connector starts as the session ending', async (t) => {
    const { manager, statuses, spawns, start } = setup(t, { modes: { alpha: 'exit-session-end-check' }, delays: [50] });
    // alpha dies during check, before its subscription ever counts as running.
    await manager.preflight();
    await start();
    assert.deepEqual(statuses.map((status) => status.status), ['session_end']);
    assert.equal(statuses[0].connector, 'alpha');
    await delay(200);
    assert.equal(spawns('alpha'), 1);
  });
});

describe('several mailboxes', () => {
  it('runs each mailbox as its own process with its own account, and one failing leaves the other running', async (t) => {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'connector-mailboxes-'));
    fs.writeFileSync(path.join(dataDir, 'email-qq.mode'), 'fail-check');
    const registry = new ProcessRegistry(logger);
    const manager = new ConnectorManager({
      connectors: ['lark', 'email'],
      emailAccounts: [{ id: 'email-gmail', configFile: '/superagent/email/gmail.json' }, { id: 'email-qq', configFile: '/superagent/email/qq.json' }],
      dataDir, projectDir: process.cwd(), maxEventBytes: 100_000,
      connectorWorkerPath: fixture, connectorRestartDelaysMs: [60_000], connectorStableAfterMs: 100,
    }, registry, logger);
    t.after(async () => {
      await manager.stop();
      await registry.stopAll(1_000);
      fs.rmSync(dataDir, { recursive: true, force: true });
    });
    const info = await manager.preflight();
    assert.deepEqual(Object.keys(info.connectors), ['lark', 'email-gmail']);
    assert.match(info.failed['email-qq'], /login expired/);
    const events = [];
    await manager.startSubscription((event) => events.push(`${event.connector}/${event.instance}`), () => {});
    await waitFor(() => events.length === 2);
    assert.deepEqual(events.sort(), ['email/email-gmail', 'lark/lark']);
    assert.equal(fs.readFileSync(path.join(dataDir, 'email-gmail.account'), 'utf8'), '/superagent/email/gmail.json');
    assert.equal(fs.readFileSync(path.join(dataDir, 'email-qq.account'), 'utf8'), '/superagent/email/qq.json');
    assert.deepEqual(Object.fromEntries(Object.entries(manager.snapshot()).map(([id, { state }]) => [id, state])),
      { lark: 'running', 'email-gmail': 'running', 'email-qq': 'restarting' });
    // A mailbox that is down still has its manifest; delivery to it is refused as unavailable.
    assert.deepEqual(manager.manifests().map(([id, manifest]) => [id, manifest.name]),
      [['lark', 'lark'], ['email-gmail', 'email'], ['email-qq', 'email']]);
    assert.equal(manager.available('email-qq'), false);
  });
});

describe('daemon with a failing Connector (mock env)', { concurrency: false }, () => {
  it('keeps Lark serving while Weixin keeps failing, and takes Weixin back after a new login', async () => {
    const env = await startMockEnv({
      config: { connectors: ['lark', 'weixin'], connectorWorkerPath: mixedFixture,
        connectorRestartDelaysMs: [100, 200], connectorStableAfterMs: 300 },
      prepare: (dir) => {
        fs.mkdirSync(path.join(dir, 'data'), { recursive: true });
        fs.writeFileSync(path.join(dir, 'data', 'weixin.mode'), 'exit-after-start');
      },
    });
    try {
      await waitFor(() => env.activities('system.connector_failed').length >= 2);
      const [failed] = env.activities('system.connector_failed');
      assert.equal(failed.metadata.connector, 'weixin');
      assert.equal(failed.metadata.code, 1);
      assert.match(failed.summary, /weixin Connector 不可用，1 秒后重连：.*session expired/);
      // The reason names the exit that happened; the summary stays short and
      // the whole text is in the detail.
      assert.equal(failed.metadata.error, 'connector weixin exited: session expired; run the QR login command again');
      assert.equal(env.daemon.shuttingDown, false);
      assert.equal(env.daemon.serviceSnapshot().ready, true);
      // Each failing worker started before it failed; none of that is recovery.
      assert.equal(env.activities('system.connector_recovered').length, 0);

      env.receive(textMessage({ messageId: 'om-while-weixin-down', text: '飞书还在' }));
      await env.waitForCodexInput(/om-while-weixin-down/);

      fs.writeFileSync(path.join(env.dir, 'data', 'weixin.mode'), 'ok');
      await waitFor(() => env.activities('system.connector_recovered').length === 1);
      assert.equal(env.activities('system.connector_recovered')[0].metadata.connector, 'weixin');
      await waitFor(() => env.daemon.serviceSnapshot().body.status === 'ready');
      assert.equal(env.daemon.serviceSnapshot().body.subscriber, 'running');
      // Failing workers also emitted before exiting; this is the recovered one.
      const spawns = fs.readFileSync(path.join(env.dir, 'data', 'weixin.spawns'), 'utf8').split('\n').filter(Boolean).length;
      await env.waitForCodexInput(new RegExp(`source=weixin kind=issue.opened event=issue-open-123-${spawns} `));
      assert.equal(env.codexSpawnArgs().length, 1, 'the runtime was never restarted');
    } finally {
      await env.stop();
    }
  });

  it('stops quietly when the session ends instead of reporting the Connector unavailable', async () => {
    const env = await startMockEnv({
      config: { connectorWorkerPath: fixture, connectorRestartDelaysMs: [50], connectorStableAfterMs: 100,
        connectorSessionEndWindowMs: 0 },
      prepare: (dir) => fs.mkdirSync(path.join(dir, 'data'), { recursive: true }),
    });
    try {
      // What the daemon records from here on, since its store closes on stop.
      const recorded = [];
      const record = env.daemon.recordSystemActivity.bind(env.daemon);
      env.daemon.recordSystemActivity = (kind, fields, suffix) => {
        recorded.push({ kind, summary: fields.summary, metadata: fields.metadata });
        return record(kind, fields, suffix);
      };
      // The daemon is up and serving; then the session goes down under it, as
      // Windows does when the user shuts the machine down.
      fs.writeFileSync(path.join(env.dir, 'data', 'lark.mode'), 'exit-session-end');
      await waitFor(() => env.daemon.shuttingDown);
      // A Connector killed by the session going down is not unavailable, and
      // nothing is retried into a session that is logging off.
      assert.equal(recorded.filter((entry) => entry.kind === 'system.connector_failed').length, 0);
      const stopping = recorded.filter((entry) => entry.kind === 'system.stopping');
      assert.equal(stopping.length, 1);
      assert.equal(stopping[0].metadata.reason, 'session_end');
      await delay(400);
      assert.equal(fs.readFileSync(path.join(env.dir, 'data', 'lark.spawns'), 'utf8')
        .split('\n').filter(Boolean).length, 1);
      assert.equal(env.daemon.serviceSnapshot({ includeState: false }).ready, false);
    } finally {
      await env.stop();
    }
  });

  it('restarts only the Lark Connector when its subscription drops', async () => {
    const env = await startMockEnv({ config: { connectorRestartDelaysMs: [100], connectorStableAfterMs: 100 } });
    try {
      env.receive(textMessage({ messageId: 'om-before-drop', text: '第一条' }));
      await env.waitForCodexInput(/om-before-drop/);
      env.killLarkSubscriber();
      await waitFor(() => env.activities('system.connector_recovered').length === 1);
      assert.equal(env.activities('system.connector_failed')[0].metadata.connector, 'lark');

      env.receive(textMessage({ messageId: 'om-after-drop', text: '第二条' }));
      await env.waitForCodexInput(/om-after-drop/);
      // The new subscription replays the event file; ingest drops the repeat.
      assert.equal(env.codexInput().filter((entry) => /om-before-drop/.test(entry.text)).length, 1);
      assert.equal(env.codexSpawnArgs().length, 1, 'the runtime was never restarted');
      assert.equal(env.daemon.shuttingDown, false);
    } finally {
      await env.stop();
    }
  });
});
