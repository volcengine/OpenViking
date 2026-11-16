import assert from 'node:assert/strict';
import test from 'node:test';
import { runtime, setup, ok } from './helpers/monitoring.mjs';

for (const config of [
  false,
  { onEvent: () => assert.fail('disabled receiver'), events: [] },
]) {
  test('disabled collection creates no source IDs, history or monitoring buffers', async () => {
    const { controller, frame } = setup('FileBrowser', config);
    frame.source = () => assert.fail('disabled source');
    let calls = 0;
    const client = runtime.bindClient(
      runtime.createOvClient({
        fetch: async () => {
          calls++;
          return ok('done');
        },
      }),
      frame,
    );
    assert.equal(
      await client.request({ method: 'GET', path: '/private/path' }),
      'done',
    );
    assert.equal(
      runtime.beginWorkflow(client, 'file_browser_directory_load'),
      undefined,
    );
    assert.equal(calls, 1);
    assert.equal(controller.roots, undefined);
    assert.equal(controller.data, undefined);
    assert.equal(controller.causes, undefined);
  });
}

test('fail-only subscriptions retain successful preceding request stages', async () => {
  const events = [];
  const { frame } = setup('FileBrowser', {
    events: ['file_browser_preview_load_fail'],
    onEvent: (event) => events.push(event),
  });
  const client = runtime.bindClient(
    runtime.createOvClient({ fetch: async () => ok({ invalid: true }) }),
    frame,
  );
  const operation = runtime.beginWorkflow(client, 'file_browser_preview_load');
  await operation.client(client).request({
    method: 'GET',
    path: '/api/v1/content/read',
    query: { uri: 'private-uri' },
  });
  operation.fail(new Error('private normalization message'), 'protocol');
  assert.equal(events.length, 1);
  const event = events[0];
  assert.equal(event.params.code, 'DATA_RESPONSE_INVALID');
  assert.equal(event.source.component.name, 'FileBrowser');
  assert.equal(event.source.component.region, 'preview');
  assert.equal(event.source.module.name, 'FileBrowser');
  assert.match(event.trace.traceId, /^(?!0{32})[0-9a-f]{32}$/);
  assert.match(event.trace.spanId, /^(?!0{16})[0-9a-f]{16}$/);
  assert.ok(
    event.params.traceSnapshot.spans.some(
      (span) => span.stage === 'client-request' && span.status === 'ready',
    ),
  );
  assert.equal(JSON.stringify(event).includes('private'), false);
});

test('receiver mutation and rejection do not corrupt history or business results', async () => {
  const { controller, frame } = setup();
  const operation = runtime.workflowForBinding(
    frame,
    'file_browser_directory_load',
  );
  controller.configure({
    onEvent: (event) => {
      event.source.component.name = 'MUTATED';
      return Promise.reject(new Error('receiver'));
    },
  });
  const client = runtime.bindClient(
    runtime.createOvClient({ fetch: async () => ok('result') }),
    frame,
  );
  assert.equal(
    await operation
      .client(client)
      .request({ method: 'GET', path: '/api/v1/fs/ls' }),
    'result',
  );
  const snapshot = operation.span.snapshot();
  assert.ok(
    snapshot.spans.every(
      (span) => span.source.component.name === 'FileBrowser',
    ),
  );
  operation.ready({ entryCount: 0, empty: true });
});

test('turning collection off clears completed histories and raw diagnostic bodies', () => {
  const { controller, frame } = setup('Console', {
    onEvent() {},
    diagnostics: { transform: () => ({}) },
  });
  const span = runtime.startSpan(frame, 'client-request');
  const observer = new runtime.RequestObserver(span, {
    method: 'POST',
    path: '/bot/v1/chat/stream',
    body: 'private-body',
  });
  observer.capture('private-response');
  span.finish();
  assert.ok(span.history.nodes.length);
  controller.configure(false);
  assert.equal(span.history.nodes.length, 0);
  assert.equal(observer.requestBody, undefined);
  assert.equal(observer.responseBody, undefined);
  assert.equal(controller.roots, undefined);
});

test('unmount clears pending diagnostic bodies without waiting for the transport', () => {
  const { frame } = setup('Console', {
    onEvent() {},
    diagnostics: { transform: () => ({}) },
  });
  const span = runtime.startSpan(frame, 'client-request');
  const observer = new runtime.RequestObserver(span, {
    method: 'POST',
    path: '/bot/v1/chat/stream',
    body: 'private-body',
  });
  frame.dispose();
  assert.equal(observer.requestBody, undefined);
  assert.equal(span.valid(), false);
});

test('trace capacity preserves root, failing path and exact omitted counts', () => {
  const { frame } = setup();
  const root = runtime.startSpan(frame, 'file_browser_directory_load');
  const ancestor = root.child('normalize-response');
  for (let i = 0; i < 90; i++) root.child('client-request').finish();
  const failed = ancestor.child('view-commit');
  const snapshot = failed.snapshot();
  assert.equal(snapshot.spans.length, 64);
  assert.equal(snapshot.omittedSpanCount, 29);
  assert.deepEqual(snapshot.causalPath, [
    root.trace.spanId,
    ancestor.trace.spanId,
    failed.trace.spanId,
  ]);
  assert.equal(snapshot.coverage, 'partial');
  assert.equal(
    snapshot.omittedByStage.reduce((sum, row) => sum + row.count, 0),
    29,
  );
});

test('separate committed monitoring scopes never share a parent trace', () => {
  const parent = setup();
  const operation = runtime.startSpan(
    parent.frame,
    'file_browser_preview_load',
  );
  const nested = setup('FilePreview');
  nested.frame.parent = operation;
  const child = runtime.startSpan(nested.frame, 'file_preview_content_prepare');
  assert.notEqual(child.trace.traceId, operation.trace.traceId);
  assert.equal(child.trace.parentSpanId, null);
  assert.ok(child.snapshot().gaps.includes('disabled-scope'));
});
