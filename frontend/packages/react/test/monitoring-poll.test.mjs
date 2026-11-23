import test from 'node:test';
import assert from 'node:assert/strict';
import { runtime as r, setup } from './helpers/monitoring.mjs';
const service = {
  getTask() {
    throw new Error('unused in observation test');
  },
};
const signal = () => new AbortController().signal;
test('poll reports each outage category once and recovers once, with no backend task failure metric', () => {
  const { frame, events } = setup('Console');
  const entry = { observedAtStart: true };
  r.observePoll(entry, frame, service, signal()).fail(
    new Error('offline'),
    false,
  );
  r.observePoll(entry, frame, service, signal()).fail(
    new Error('offline'),
    false,
  );
  r.observePoll(entry, frame, service, signal()).fail(
    new Error('bad response'),
    true,
  );
  r.observePoll(entry, frame, service, signal()).fail(
    new Error('bad response'),
    true,
  );
  r.observePoll(entry, frame, service, signal()).success();
  r.observePoll(entry, frame, service, signal()).success();
  assert.deepEqual(
    events.map((e) => e.name),
    [
      'task_poll_request_fail',
      'task_poll_protocol_fail',
      'task_poll_recovered',
    ],
  );
  assert.equal(new Set(events.map((e) => e.params.outageId)).size, 1);
  assert.equal(events.at(-1).params.failureCount, 4);
  assert.deepEqual(events.at(-1).params.failedCategories, [
    'request',
    'protocol',
  ]);
});
test('disable clears task metadata immediately and suppresses old in-flight recovery', () => {
  const { frame, controller, events } = setup('Console');
  const entry = { observedAtStart: true };
  r.observePoll(entry, frame, service, signal()).fail(
    new Error('offline'),
    false,
  );
  const pending = r.observePoll(entry, frame, service, signal());
  controller.configure(false);
  assert.equal(entry.monitoring, undefined);
  assert.equal(entry.monitoringRelease, undefined);
  assert.equal(entry.origin, undefined);
  assert.equal(entry.observedAtStart, false);
  controller.configure({
    onEvent(event) {
      events.push(event);
    },
  });
  pending.success();
  r.observePoll(entry, frame, service, signal()).success();
  assert.equal(events.length, 1);
});
test('render attribution uses the exact committed data, including completed segments from cancelled work', () => {
  const { frame, events } = setup('Console');
  const data = {};
  const first = r.startSpan(frame, 'console-chat-reply');
  first.child('normalize-response').finish();
  r.tagData(frame, data, [first]);
  first.finish('cancelled', 'interrupted');
  const unrelated = r.startSpan(frame, 'console_session_history_load');
  unrelated?.finish();
  assert.deepEqual(r.readData(frame, [data]), [first]);
  const child = new r.MonitoringFrame('VikingBot', frame.controller, frame);
  child.live = true;
  child.inputs = () => r.readData(frame, [data]);
  child.renderError(new Error('render committed cancelled data'));
  const failure = events.at(-1);
  assert.equal(failure.name, 'viking_bot_render_error');
  assert.equal(
    failure.params.traceSnapshot.links[0].traceId,
    first.trace.traceId,
  );
  assert.equal(
    failure.params.traceSnapshot.links.some(
      (link) => link.traceId === unrelated.trace.traceId,
    ),
    false,
  );
  assert.equal(
    failure.params.traceSnapshot.links[0].causalSummary.some(
      (s) => s.status === 'cancelled',
    ),
    true,
  );
});

test('a shared cached Error in sibling component instances does not merge their faults', () => {
  const { frame, events } = setup('FileBrowser');
  const parent = r.startSpan(frame, 'file_browser_preview_load');
  const error = new Error('cached failure');
  for (let i = 0; i < 2; i++) {
    const sibling = new r.MonitoringFrame(
      'FilePreview',
      frame.controller,
      frame,
    );
    sibling.live = true;
    sibling.parent = parent;
    r.startSpan(sibling, 'file_preview_viewer_load').fail(
      'file_preview_viewer_load_fail',
      error,
      {},
      'resource-load',
    );
  }
  assert.equal(events.length, 2);
  assert.notEqual(
    events[0].source.component.instanceId,
    events[1].source.component.instanceId,
  );
  assert.notEqual(events[0].params.incidentId, events[1].params.incidentId);
  assert.equal(events[1].params.faultRole, 'origin');
});
