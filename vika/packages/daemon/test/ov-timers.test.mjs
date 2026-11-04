import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { OVTimerRepository } from '../src/ov-timers.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { TimerConnector } from '../src/timers.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

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
  for (const operation of ['create', 'cancel']) {
    it(`refreshes cached state after a committed ${operation} loses its response`, async (context) => {
      const { client, repository } = await setup(context);
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
      await assert.rejects(operation === 'create' ? repository.create(input) : repository.cancel(input.timerId), /response lost/);
      assert.equal(Boolean(repository.get(input.timerId)), operation === 'create');
      await repository.create({ ...input, timerId: 'tm_second', createdFromEventId: 'evt-second' });
      const restored = await new OVTimerRepository(client, 'primary').load();
      assert.equal(Boolean(restored.get(input.timerId)), operation === 'create');
      assert.equal(restored.get('tm_second').timerId, 'tm_second');
    });
  }

  it('refreshes before further writes when the first refresh fails', async (context) => {
    const { client, repository, state } = await setup(context);
    await repository.create(input);
    const json = client.json.bind(client);
    let loseResponse = true;
    client.json = async (route, body) => {
      const result = await json(route, body);
      if (body && loseResponse) {
        loseResponse = false;
        state.down = true;
        throw new Error('Write response lost');
      }
      return result;
    };
    await assert.rejects(repository.cancel(input.timerId), /response lost/);
    const second = { ...input, timerId: 'tm_second', createdFromEventId: 'evt-second' };
    await assert.rejects(repository.create(second), /HTTP 503/);
    state.down = false;
    await repository.create(second);
    assert.equal(repository.get(input.timerId), null);
    const restored = await new OVTimerRepository(client, 'primary').load();
    assert.deepEqual(restored.listActive().map((timer) => timer.timerId), ['tm_second']);
  });

  it('does not fire a cancelled timer after its response and cache refresh fail', async (context) => {
    const { client, repository, state } = await setup(context);
    await repository.create(input);
    const json = client.json.bind(client);
    let loseResponse = true;
    client.json = async (route, body) => {
      const result = await json(route, body);
      if (body && loseResponse) {
        loseResponse = false;
        state.down = true;
        throw new Error('Write response lost');
      }
      return result;
    };
    await assert.rejects(repository.cancel(input.timerId), /response lost/);
    const submitted = [];
    const connector = new TimerConnector({
      repository, now: () => nowMs, autoStart: false,
      submitEvent: async (event) => submitted.push(event),
    });
    context.after(() => connector.stop());
    await assert.rejects(connector.fireDueOnce(), /HTTP 503/);
    assert.equal(submitted.length, 0);
    state.down = false;
    assert.equal(await connector.fireDueOnce(), false);
    assert.equal(submitted.length, 0);
    assert.equal(repository.getOccurrenceCheck('tm_test:1790000000000'), null);
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
    state.files.set('viking://user/alice/resources/ld/primary/timers-state.json', '{broken');
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
