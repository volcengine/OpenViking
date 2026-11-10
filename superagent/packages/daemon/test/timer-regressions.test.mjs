import assert from 'node:assert/strict';
import { it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { Store } from '../src/store.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { TimerConnector, TimerRepository } from '../src/timers.mjs';
import { OVTimerRepository } from '../src/ov-timers.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { fakeOV } from './ov-server-fixture.mjs';
import { fakeConnectors } from './action-fixture.mjs';

const nowMs = 1_790_000_000_000;
const logger = { info() {}, warn() {}, error() {}, debug() {} };

for (const backend of ['sqlite', 'ov']) {
  async function setup(context) {
    const store = new Store(':memory:', logger);
    context.after(() => store.close());
    store.initializeCognition('codex', 'token');
    let repository;
    let client;
    if (backend === 'sqlite') repository = new TimerRepository(store.db);
    else {
      const server = await fakeOV(context);
      client = new OVConnection({ url: server.url, user: 'alice' });
      repository = await new OVTimerRepository(client, 'primary').load();
    }
    await repository.create({
      timerId: 'tm_test', title: 'Reminder', text: 'Check PR', timezone: 'UTC',
      schedule: { type: 'once', run_at_ms: nowMs }, nextRunAtMs: nowMs,
      maxRuns: 1, delivery: { mode: 'owner' },
      createdFromSource: 'lark', createdFromEventId: 'evt-test', createdAtMs: nowMs,
    });
    let clock = nowMs;
    const events = [];
    const connector = new TimerConnector({
      repository, now: () => clock, autoStart: false,
      submitEvent: async (event) => { events.push(event); store.ingest(normalizeEvent(event)); },
    });
    context.after(() => connector.stop());
    await connector.fireDueOnce();
    const connectors = fakeConnectors({
      owners: { lark: { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } } },
    });
    const service = new ActionService({
      config: { channel: 'lark', maxReplyChars: 1_000 }, store, connectors, logger, timers: repository,
    });
    return { store, repository, client, connector, connectors, service, events,
      advance: () => { clock += 300_000; } };
  }

  it(`${backend} stops scheduling after five completion checks`, async (context) => {
    const state = await setup(context);
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      state.advance();
      assert.equal(await state.connector.fireDueOnce(), true);
    }
    const check = state.repository.getOccurrenceCheck(`tm_test:${nowMs}`);
    assert.equal(check.status, 'exhausted');
    assert.equal(check.attempt, 5);
    assert.equal(state.repository.nextWakeAtMs(), 1_790_606_600_001);
    state.advance();
    assert.equal(await state.connector.fireDueOnce(), false);
    assert.equal(state.events.length, 6);
    await state.repository.markOccurrenceCompleted(check.occurrenceId);
    await state.repository.rescheduleOccurrenceCheck(check.occurrenceId, { attempt: 5, nextCheckAtMs: nowMs });
    assert.equal(state.repository.getOccurrenceCheck(check.occurrenceId).status, 'completed');
    assert.equal(await state.connector.fireDueOnce(), true);
    assert.equal(state.repository.getOccurrenceCheck(check.occurrenceId), null);
  });

  it(`${backend} cleans exhausted checks after seven days without submitting events`, async (context) => {
    const state = await setup(context);
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      state.advance();
      await state.connector.fireDueOnce();
    }
    state.connector.now = () => 1_790_606_600_000;
    assert.equal(await state.connector.fireDueOnce(), false);
    assert.equal(state.repository.getOccurrenceCheck('tm_test:1790000000000').status, 'exhausted');
    state.connector.now = () => 1_790_606_600_001;
    assert.equal(await state.connector.fireDueOnce(), false);
    assert.equal(state.repository.getOccurrenceCheck('tm_test:1790000000000'), null);
    assert.equal(state.repository.nextWakeAtMs(), null);
    assert.equal(state.events.length, 6);
    if (backend === 'ov') {
      const restored = await new OVTimerRepository(state.client, 'primary').load();
      assert.equal(restored.getOccurrenceCheck('tm_test:1790000000000'), null);
    }
  });

  it(`${backend} finishes from the current check when the original event is absent`, async (context) => {
    const state = await setup(context);
    state.store.db.exec('DELETE FROM connector_events');
    if (backend === 'ov') {
      state.repository = await new OVTimerRepository(state.client, 'primary').load();
      state.connector.repository = state.repository;
      state.service.timers = state.repository;
    }
    state.advance();
    await state.connector.fireDueOnce();
    const event = state.events.at(-1);
    const result = await state.service.call('finish_timer_occurrence', {
      occurrence_id: event.data.timer.occurrence_id,
      delivery: { tool: 'notify_owner', input: { decision_id: event.id, markdown: 'Done' } },
    }, { authority: 'token' });
    assert.equal(result.data.completed, true);
    assert.equal(state.connectors.calls.length, 1);
  });

  it(`${backend} does not resend a delivered result when completion retries on a newer check`, async (context) => {
    const state = await setup(context);
    const complete = state.repository.markOccurrenceCompleted.bind(state.repository);
    state.repository.markOccurrenceCompleted = async () => { throw new Error('completion write failed'); };
    const finish = () => {
      const event = state.events.at(-1);
      return state.service.call('finish_timer_occurrence', {
        occurrence_id: event.data.timer.occurrence_id,
        delivery: { tool: 'notify_owner', input: { decision_id: event.id, markdown: 'Done' } },
      }, { authority: 'token' });
    };
    await assert.rejects(finish(), /completion write failed/);
    state.advance();
    await state.connector.fireDueOnce();
    state.repository.markOccurrenceCompleted = complete;
    assert.equal((await finish()).data.completed, true);
    assert.equal(state.connectors.calls.length, 1);
  });
}
