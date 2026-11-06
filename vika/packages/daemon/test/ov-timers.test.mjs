import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { OVTimerRepository } from '../src/ov-timers.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { TimerConnector } from '../src/timers.mjs';
import { fakeOV } from './ov-server-fixture.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent } from './event-fixture.mjs';
import { fakeConnectors } from './action-fixture.mjs';

const nowMs = 1_790_000_000_000;
const input = {
  timerId: 'tm_test', title: 'Reminder', text: 'Check PR', timezone: 'UTC',
  schedule: { type: 'interval', interval_ms: 60_000, anchor_at_ms: nowMs },
  nextRunAtMs: nowMs, maxRuns: 2, delivery: { mode: 'owner' },
  createdFromSource: 'lark', createdFromEventId: 'evt-test', createdAtMs: nowMs,
};

async function setup(context) {
  const server = await fakeOV(context);
  const client = new OVConnection({ url: server.url, user: 'alice' });
  const repository = await new OVTimerRepository(client, 'primary').load();
  return { ...server, client, repository };
}

describe('OV timer storage', () => {
  it('uses the key identity and keeps shared users separated by instance', async (context) => {
    const { state, url } = await fakeOV(context, { apiKeys: { 'asd-key': 'owner.asd', 'manbo-key': 'owner.manbo' } });
    const asd = new OVConnection({ url, api_key: 'asd-key', user: 'owner' });
    const manbo = new OVConnection({ url, api_key: 'manbo-key' });
    const first = await new OVTimerRepository(asd, 'primary').load();
    const otherUser = await new OVTimerRepository(manbo, 'primary').load();
    const sharedUser = await new OVTimerRepository(asd, 'secondary').load();
    await first.create(input);
    await otherUser.create({ ...input, title: 'Another user' });
    await sharedUser.create({ ...input, title: 'Another instance' });
    assert.equal((await new OVTimerRepository(asd, 'primary').load()).get(input.timerId).title, 'Reminder');
    assert.equal((await new OVTimerRepository(manbo, 'primary').load()).get(input.timerId).title, 'Another user');
    assert.equal((await new OVTimerRepository(asd, 'secondary').load()).get(input.timerId).title, 'Another instance');
    assert.deepEqual([...state.files.keys()].sort(), [
      'viking://user/owner.asd/resources/vika/primary/timers-state.json',
      'viking://user/owner.asd/resources/vika/secondary/timers-state.json',
      'viking://user/owner.manbo/resources/vika/primary/timers-state.json',
    ]);
    assert.ok(state.requests.filter(({ body }) => body?.uri).every(({ body }) => body.uri.startsWith('viking://~/')));
  });

  it('loads existing timers from the vika namespace', async (context) => {
    const server = await fakeOV(context);
    const client = new OVConnection({ url: server.url, user: 'alice' });
    server.state.files.set('viking://user/alice/resources/vika/primary/timers-state.json', JSON.stringify({
      timers: [{ ...input, runCount: 0, lastRunAtMs: null }], timer_occurrence_checks: [],
    }));
    const repository = await new OVTimerRepository(client, 'primary').load();
    assert.equal(repository.get(input.timerId)?.title, 'Reminder');
  });

  it('waits for indexing before consecutive writes and immediate completion', async (context) => {
    const { client, repository } = await setup(context);
    const json = client.json.bind(client);
    let locked = false;
    client.json = async (route, body) => {
      if (!body) return json(route);
      if (locked) throw new Error('File is locked by indexing');
      const result = await json(route, body);
      locked = true;
      const indexed = new Promise((resolve) => setTimeout(() => { locked = false; resolve(); }, 20));
      if (body.wait) await indexed;
      return result;
    };
    await Promise.all([
      repository.create(input),
      repository.create({ ...input, timerId: 'tm_second', createdFromEventId: 'evt-second' }),
    ]);
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false, submitEvent: async () => {},
    });
    await connector.fireDueOnce();
    const occurrenceId = 'tm_test:1790000000000';
    await repository.markOccurrenceCompleted(occurrenceId);
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.equal(restored.get(input.timerId).runCount, 1);
    assert.equal(restored.getOccurrenceCheck(occurrenceId).status, 'completed');
    assert.equal(restored.listActive().length, 2);
  });

  it('does not write when timer or check state is unchanged', async (context) => {
    const { repository, state } = await setup(context);
    await repository.create(input);
    const occurrence = {
      occurrenceId: 'tm_test:1790000000000', eventId: 'timer:test', nextCheckAtMs: nowMs, event: {},
    };
    await repository.createOccurrenceCheck(occurrence);
    await repository.markOccurrenceCompleted(occurrence.occurrenceId);
    state.requests.length = 0;
    await repository.create(input);
    await repository.createOccurrenceCheck(occurrence);
    await repository.createOccurrenceCheck({ ...occurrence, timer: { ...input, nextRunAtMs: nowMs - 1 } });
    await repository.cancel('tm_missing');
    await repository.markOccurrenceCompleted(occurrence.occurrenceId);
    await repository.rescheduleOccurrenceCheck(occurrence.occurrenceId, { attempt: 1, nextCheckAtMs: nowMs + 300_000 });
    assert.equal(state.requests.filter((request) => request.route === 'POST /api/v1/content/write').length, 0);
  });

  it('expires exhausted checks after seven days even without active timers', async (context) => {
    const { client, repository } = await setup(context);
    const baseMs = Date.now();
    const checks = [
      { occurrenceId: 'tm_old:1', status: 'exhausted', nextCheckAtMs: baseMs - 604_800_001 },
      { occurrenceId: 'tm_legacy:1', status: 'exhausted', nextCheckAtMs: baseMs - 604_800_001 },
      { occurrenceId: 'tm_recent:1', status: 'exhausted', nextCheckAtMs: baseMs },
      { occurrenceId: 'tm_pending:1', status: 'pending', nextCheckAtMs: baseMs + 900_000_000 },
    ];
    for (const check of checks) {
      await repository.createOccurrenceCheck({ ...check, eventId: check.occurrenceId, event: {} });
    }
    await repository.mutate((state) => {
      for (const check of checks) Object.assign(state.timer_occurrence_checks.find((entry) => entry.occurrenceId === check.occurrenceId), check);
    });
    assert.ok(repository.nextWakeAtMs() <= baseMs);
    const connector = new TimerConnector({
      repository, now: () => baseMs, autoStart: false, submitEvent: async () => assert.fail('No reminder is due'),
    });
    await connector.fireDueOnce();
    let restored = await new OVTimerRepository(client, 'primary').load();
    assert.equal(restored.getOccurrenceCheck('tm_old:1'), null);
    assert.equal(restored.getOccurrenceCheck('tm_legacy:1'), null);
    assert.equal(restored.getOccurrenceCheck('tm_recent:1').status, 'exhausted');
    assert.equal(restored.getOccurrenceCheck('tm_pending:1').status, 'pending');
    assert.equal(repository.nextWakeAtMs(), baseMs + 604_800_001);
    connector.now = () => baseMs + 604_800_001;
    await connector.fireDueOnce();
    restored = await new OVTimerRepository(client, 'primary').load();
    assert.equal(restored.getOccurrenceCheck('tm_recent:1'), null);
    assert.equal(restored.getOccurrenceCheck('tm_pending:1').status, 'pending');
  });

  it('does not recreate checks when cancellation commits before queued scheduling', async (context) => {
    const { client, repository } = await setup(context);
    await repository.create(input);
    const json = client.json.bind(client);
    let release;
    let writeStarted;
    const started = new Promise((resolve) => { writeStarted = resolve; });
    const pending = new Promise((resolve) => { release = resolve; });
    let delayWrite = true;
    client.json = async (route, body) => {
      if (body && delayWrite) {
        delayWrite = false;
        writeStarted();
        await pending;
      }
      return json(route, body);
    };
    const submitted = [];
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false,
      submitEvent: async (event) => submitted.push(event),
    });
    context.after(() => connector.stop());
    const cancelled = repository.cancel(input.timerId);
    await started;
    const firing = connector.fireDueOnce();
    release();
    assert.equal(await cancelled, true);
    await firing;
    for (let attempt = 1; attempt <= 6; attempt += 1) {
      connector.now = () => nowMs + attempt * 300_000;
      assert.equal(await connector.fireDueOnce(), false);
    }
    assert.equal(submitted.length, 0);
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.equal(restored.get(input.timerId), null);
    assert.equal(restored.getOccurrenceCheck(`${input.timerId}:${nowMs}`), null);
    assert.equal(restored.nextWakeAtMs(), null);
  });

  it('serializes a delayed refresh with new timer writes', async (context) => {
    const { client, repository } = await setup(context);
    await repository.create({ ...input, timerId: 'tm_existing', createdFromEventId: 'evt-existing' });
    const json = client.json.bind(client);
    let release;
    let readStarted;
    const started = new Promise((resolve) => { readStarted = resolve; });
    const pending = new Promise((resolve) => { release = resolve; });
    let delayRead = true;
    let refreshing = false;
    let wroteDuringRefresh = false;
    client.json = async (route, body) => {
      if (body && refreshing) wroteDuringRefresh = true;
      const result = await json(route, body);
      if (!body && delayRead) {
        delayRead = false;
        refreshing = true;
        readStarted();
        await pending;
        refreshing = false;
      }
      return result;
    };
    const refresh = repository.load();
    await started;
    const created = repository.create(input);
    await new Promise((resolve) => setImmediate(resolve));
    release();
    await Promise.all([refresh, created]);
    assert.equal(wroteDuringRefresh, false);
    assert.equal(repository.get(input.timerId)?.timerId, input.timerId);
    await repository.create({ ...input, timerId: 'tm_second', createdFromEventId: 'evt-second' });
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.deepEqual(restored.listActive().map((timer) => timer.timerId), ['tm_existing', 'tm_test', 'tm_second']);
  });

  it('wakes an idle scheduler when a repeated create finds a loaded timer', async (context) => {
    const { repository } = await setup(context);
    await repository.create({ ...input, createdFromEventId: 'evt-create', nextRunAtMs: Date.now() + 3_600_000 });
    const logger = { info() {}, warn() {}, debug() {}, error() {} };
    const store = new Store(':memory:', logger);
    context.after(() => store.close());
    store.initializeCognition('codex', 'token');
    store.ingest(chatEvent({ eventId: 'evt-create' }));
    const connector = new TimerConnector({ repository, autoStart: false, submitEvent: async () => {} });
    context.after(() => connector.stop());
    const service = new ActionService({
      config: { channel: 'lark' }, store, logger, timers: repository,
      connectors: fakeConnectors(), onTimersChanged: () => connector.wake(),
    });
    const result = await service.call('create_timer', {
      decision_id: 'evt-create', title: 'Reminder', text: 'Check PR',
      schedule: { type: 'once', run_at_ms: Date.now() + 3_600_000 },
      delivery: { mode: 'owner' },
    }, { authority: 'token' });
    assert.equal(result.data.duplicate, true);
    assert.notEqual(connector.timer, null);
  });

  for (const operation of ['create', 'cancel']) {
    it(`retries ${operation} without reading back after losing its write response`, async (context) => {
      const { client, repository, state } = await setup(context);
      if (operation === 'cancel') await repository.create(input);
      const json = client.json.bind(client);
      let loseResponse = true;
      client.json = async (route, body) => {
        const result = await json(route, body);
        if (body && loseResponse) {
          loseResponse = false;
          throw new Error('Write response lost');
        }
        return result;
      };
      const requestCount = state.requests.length;
      const result = await (operation === 'create' ? repository.create(input) : repository.cancel(input.timerId));
      assert.equal(operation === 'create' ? result.timerId : result, operation === 'create' ? input.timerId : true);
      assert.equal(state.requests.length, requestCount + 2);
      assert.deepEqual(state.requests.slice(requestCount).map((request) => request.route), [
        'POST /api/v1/content/write', 'POST /api/v1/content/write',
      ]);
      const restored = await new OVTimerRepository(client, 'primary').load();
      assert.equal(Boolean(restored.get(input.timerId)), operation === 'create');
    });
  }

  it('wakes an idle scheduler after retrying a create whose response was lost', async (context) => {
    const { client, repository } = await setup(context);
    const logger = { info() {}, warn() {}, debug() {}, error() {} };
    const store = new Store(':memory:', logger);
    context.after(() => store.close());
    store.initializeCognition('codex', 'token');
    store.ingest(chatEvent({ eventId: 'evt-create' }));
    const connector = new TimerConnector({ repository, autoStart: false, submitEvent: async () => {} });
    context.after(() => connector.stop());
    connector.wake();
    assert.equal(connector.timer, null);
    const service = new ActionService({
      config: { channel: 'lark' }, store, logger, timers: repository,
      connectors: fakeConnectors(), onTimersChanged: () => connector.wake(),
    });
    const json = client.json.bind(client);
    let loseResponse = true;
    client.json = async (route, body) => {
      const result = await json(route, body);
      if (body && loseResponse) {
        loseResponse = false;
        throw new Error('Write response lost');
      }
      return result;
    };
    const request = {
      decision_id: 'evt-create', title: 'Reminder', text: 'Check PR',
      schedule: { type: 'once', run_at_ms: Date.now() + 3_600_000 },
      delivery: { mode: 'owner' },
    };
    const result = await service.call('create_timer', request, { authority: 'token' });
    assert.equal(result.data.duplicate, false);
    assert.notEqual(connector.timer, null);
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.deepEqual(restored.listActive().map((timer) => timer.timerId), [result.data.timer_id]);
  });

  it('reports failure after three lost write responses even when content was saved', async (context) => {
    const { client, repository, state } = await setup(context);
    const json = client.json.bind(client);
    const payloads = [];
    client.json = async (route, body) => {
      if (!body) assert.fail('Failed writes must not be confirmed by reading back');
      payloads.push(body.content);
      await json(route, body);
      throw new Error('Write response lost');
    };
    state.requests.length = 0;
    await assert.rejects(repository.create(input), /Write response lost/);
    assert.equal(payloads.length, 3);
    assert.equal(new Set(payloads).size, 1);
    assert.deepEqual(state.requests.map((request) => request.route), [
      'POST /api/v1/content/write', 'POST /api/v1/content/write', 'POST /api/v1/content/write',
    ]);
    assert.equal(repository.get(input.timerId), null);
    assert.equal(repository.needsRefresh, true);
  });

  it('retries an uncommitted write internally and bounds persistent failures', async (context) => {
    const { client, repository } = await setup(context);
    const json = client.json.bind(client);
    let writes = 0;
    client.json = async (route, body) => {
      if (body && ++writes === 1) throw new Error('Connection lost before writing');
      return json(route, body);
    };
    assert.equal((await repository.create(input)).timerId, input.timerId);
    assert.equal(writes, 2);
    writes = 0;
    client.json = async (route, body) => {
      if (body) { writes += 1; throw new Error('Persistent outage'); }
      return json(route, body);
    };
    await assert.rejects(repository.cancel(input.timerId), /Persistent outage/);
    assert.equal(writes, 3);
  });

  it('refreshes uncertain state before another mutation instead of losing a committed timer', async (context) => {
    const { client, repository } = await setup(context);
    const json = client.json.bind(client);
    client.json = async (route, body) => {
      if (body) {
        await json(route, body);
        throw new Error('Write response lost');
      }
      throw new Error('Read unavailable');
    };
    await assert.rejects(repository.create(input));
    client.json = json;
    await repository.create({ ...input, timerId: 'tm_second', createdFromEventId: 'evt-second' });
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.deepEqual(restored.listActive().map((timer) => timer.timerId), ['tm_test', 'tm_second']);
  });

  it('does not fire a cancelled timer while its write result remains uncertain', async (context) => {
    const { client, repository } = await setup(context);
    await repository.create(input);
    const json = client.json.bind(client);
    client.json = async (route, body) => {
      if (body) {
        await json(route, body);
        throw new Error('Write response lost');
      }
      throw new Error('Read unavailable');
    };
    await assert.rejects(repository.cancel(input.timerId));
    const submitted = [];
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false,
      submitEvent: async (event) => submitted.push(event),
    });
    context.after(() => connector.stop());
    await assert.rejects(connector.fireDueOnce());
    assert.equal(submitted.length, 0);
    client.json = json;
    assert.equal(await connector.fireDueOnce(), false);
  });

  it('keeps polling an uncertain first create and fires it after OV recovers', async (context) => {
    const { client, repository } = await setup(context);
    const logger = { info() {}, warn() {}, debug() {}, error() {} };
    const store = new Store(':memory:', logger);
    context.after(() => store.close());
    store.initializeCognition('codex', 'token');
    store.ingest(chatEvent({ eventId: 'evt-create' }));
    const submitted = [];
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false,
      submitEvent: async (event) => submitted.push(event),
    });
    context.after(() => connector.stop());
    const service = new ActionService({
      config: { channel: 'lark' }, store, logger, timers: repository, clock: () => nowMs,
      connectors: fakeConnectors(), onTimersChanged: () => connector.wake(),
    });
    const json = client.json.bind(client);
    client.json = async (route, body) => {
      if (body) {
        await json(route, body);
        throw new Error('Write response lost');
      }
      throw new Error('Read unavailable');
    };
    await assert.rejects(service.call('create_timer', {
      decision_id: 'evt-create', title: 'Reminder', text: 'Check PR',
      schedule: { type: 'once', run_at_ms: nowMs }, delivery: { mode: 'owner' },
    }, { authority: 'token' }));
    assert.equal(repository.needsRefresh, true);
    assert.notEqual(connector.timer, null);
    await connector.tick();
    assert.equal(submitted.length, 0);
    assert.notEqual(connector.timer, null);
    client.json = json;
    await connector.tick();
    assert.equal(submitted.length, 1);
    assert.equal(repository.needsRefresh, false);
  });

  it('persists timers and completion checks and restores them for the same instance', async (context) => {
    const { client, repository } = await setup(context);
    await repository.create(input);
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false,
      submitEvent: async () => {},
    });
    await connector.fireDueOnce();
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.equal(restored.get('tm_test').runCount, 1);
    assert.equal(restored.nextWakeAtMs(), nowMs + 60_000);
    assert.equal(restored.getOccurrenceCheck('tm_test:1790000000000').status, 'pending');
    await restored.markOccurrenceCompleted('tm_test:1790000000000');
    const completed = await new OVTimerRepository(client, 'primary').load();
    assert.equal(completed.getOccurrenceCheck('tm_test:1790000000000').status, 'completed');
    connector.repository = completed;
    connector.now = () => nowMs + 300_000;
    await connector.fireDueOnce();
    assert.equal(completed.get('tm_test'), null);
    await connector.fireDueOnce();
    assert.equal(completed.getOccurrenceCheck('tm_test:1790000000000'), null);
    await completed.cancel('tm_test');
  });

  it('isolates users and instances and keeps queries local after loading', async (context) => {
    const { client, repository, state, url } = await setup(context);
    await repository.create(input);
    assert.equal(repository.findByDecision('lark', 'evt-test').timerId, 'tm_test');
    assert.equal((await new OVTimerRepository(client, 'work').load()).listActive().length, 0);
    const bob = new OVConnection({ url, user: 'bob' });
    assert.equal((await new OVTimerRepository(bob, 'primary').load()).listActive().length, 0);
    state.down = true;
    assert.equal(repository.listActive().length, 1);
    assert.equal(repository.nextDue(nowMs - 1), null);
  });

  it('serializes concurrent writes and does not publish a failed mutation', async (context) => {
    const { client, repository, state } = await setup(context);
    await Promise.all([
      repository.create(input),
      repository.create({ ...input, timerId: 'tm_second', createdFromEventId: 'evt-second' }),
    ]);
    assert.equal((await new OVTimerRepository(client, 'primary').load()).listActive().length, 2);
    state.down = true;
    await assert.rejects(repository.cancel('tm_test'), /HTTP 503/);
    assert.equal(repository.get('tm_test').timerId, 'tm_test');
    state.down = false;
    assert.equal(await repository.cancel('tm_test'), true);
    assert.equal((await new OVTimerRepository(client, 'primary').load()).listActive().length, 1);
  });

  it('rejects outages and malformed state instead of treating them as an empty file', async (context) => {
    const { client, state } = await setup(context);
    state.down = true;
    await assert.rejects(new OVTimerRepository(client, 'primary').load(), /HTTP 503/);
    state.down = false;
    state.files.set('viking://user/alice/resources/vika/primary/timers-state.json', '{broken');
    await assert.rejects(new OVTimerRepository(client, 'primary').load(), /malformed/);
  });

  it('keeps a completed check completed when a concurrent reminder advances its check time', async (context) => {
    const { repository } = await setup(context);
    await repository.createOccurrenceCheck({
      occurrenceId: 'tm_test:1790000000000', eventId: 'timer:test', nextCheckAtMs: nowMs, event: {},
    });
    await Promise.all([
      repository.markOccurrenceCompleted('tm_test:1790000000000'),
      repository.rescheduleOccurrenceCheck('tm_test:1790000000000', { attempt: 1, nextCheckAtMs: nowMs + 300_000 }),
    ]);
    assert.equal(repository.getOccurrenceCheck('tm_test:1790000000000').status, 'completed');
  });

  it('keeps the scheduler alive after a storage outage without issuing the event', async (context) => {
    const { repository, state } = await setup(context);
    await repository.create(input);
    state.down = true;
    const submitted = [];
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false,
      submitEvent: async (event) => submitted.push(event),
    });
    context.after(() => connector.stop());
    await assert.doesNotReject(connector.tick());
    assert.equal(connector.running, false);
    assert.equal(submitted.length, 0);
    assert.equal(repository.get('tm_test').runCount, 0);
    state.down = false;
    await connector.fireDueOnce();
    assert.equal(submitted.length, 1);
  });
});
