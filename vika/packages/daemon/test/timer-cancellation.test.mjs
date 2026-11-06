import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { Store } from '../src/store.mjs';
import { TimerRepository } from '../src/timers.mjs';
import { OVTimerRepository } from '../src/ov-timers.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

const nowMs = 1_790_000_000_000;
const input = {
  timerId: 'tm_test', title: 'Reminder', text: 'Check PR', timezone: 'UTC',
  schedule: { type: 'interval', interval_ms: 60_000, anchor_at_ms: nowMs },
  nextRunAtMs: nowMs, maxRuns: null, delivery: { mode: 'owner' },
  createdFromSource: 'lark', createdFromEventId: 'evt-test', createdAtMs: nowMs,
};

for (const backend of ['sqlite', 'ov']) {
  describe(`${backend} timer cancellation`, () => {
    async function setup(context) {
      if (backend === 'sqlite') {
        const store = new Store(':memory:', { warn() {}, info() {} });
        context.after(() => store.close());
        return new TimerRepository(store.db);
      }
      const server = await fakeOV(context);
      return new OVTimerRepository(new OVConnection({ url: server.url, user: 'alice' }), 'primary').load();
    }

    async function check(repository, occurrenceId) {
      return repository.createOccurrenceCheck({
        occurrenceId, eventId: `timer:${occurrenceId}`, nextCheckAtMs: nowMs + 300_000, event: {},
      });
    }

    it('cancels the active timer and its checks without touching another timer', async (context) => {
      const repository = await setup(context);
      await repository.create(input);
      await check(repository, 'tm_test:1');
      await check(repository, 'tm_test:2');
      await repository.markOccurrenceCompleted('tm_test:2');
      await check(repository, 'tm_test_other:1');
      assert.equal(await repository.cancel('tm_test'), true);
      assert.equal(repository.get('tm_test'), null);
      assert.equal(repository.getOccurrenceCheck('tm_test:1'), null);
      assert.equal(repository.getOccurrenceCheck('tm_test:2'), null);
      assert.equal(repository.getOccurrenceCheck('tm_test_other:1').status, 'pending');
      assert.equal(await repository.cancel('tm_test'), false);
    });

    for (const schedule of [
      { type: 'once', run_at_ms: nowMs },
      input.schedule,
    ]) {
      it(`retains the last ${schedule.type} check on natural expiry, but removes it on explicit cancel`, async (context) => {
        const repository = await setup(context);
        const timer = await repository.create({ ...input, schedule, maxRuns: 1 });
        await check(repository, 'tm_test:1');
        await repository.finishOccurrence(timer, { scheduledTimeMs: nowMs, completedAtMs: nowMs, runCount: 1 });
        assert.equal(repository.get('tm_test'), null);
        assert.equal(repository.getOccurrenceCheck('tm_test:1').status, 'pending');
        assert.equal(await repository.cancel('tm_test'), true);
        assert.equal(repository.nextOccurrenceCheck(), null);
      });
    }
  });
}
