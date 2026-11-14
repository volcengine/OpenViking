import assert from 'node:assert/strict';
import test from 'node:test';
import { runtime, setup, ok } from './helpers/monitoring.mjs';

const make = (frame, fetch, options) =>
  runtime.bindClient(
    runtime.createOvClient({
      baseURL: 'https://private.invalid/gateway',
      fetch,
      ...options,
    }),
    frame,
  );

test('HTTP 200 business errors correlate request and module failure without raw content', async () => {
  const { frame, events } = setup();
  const client = make(frame, async () =>
    Response.json(
      {
        status: 'error',
        error: { code: 'NETWORK_ERROR', message: 'private-server-message' },
      },
      { headers: { 'x-request-id': 'request-7' } },
    ),
  );
  const operation = runtime.beginWorkflow(
    client,
    'file_browser_directory_load',
  );
  try {
    await operation.client(client).request({
      method: 'POST',
      path: '/api/v1/fs/ls',
      body: { key: 'private-body' },
    });
  } catch (error) {
    operation.fail(error);
  }
  assert.deepEqual(
    events.map((event) => event.name),
    ['client_request_fail', 'file_browser_directory_load_fail'],
  );
  const [request, module] = events;
  assert.equal(request.params.errorKind, 'business');
  assert.equal(module.params.incidentId, request.params.incidentId);
  assert.equal(module.params.causeEventId, request.eventId);
  assert.equal(module.params.faultRole, 'propagation');
  assert.equal(request.params.requestId, 'request-7');
  assert.equal(request.params.httpStatus, 200);
  assert.equal(JSON.stringify(events).includes('private'), false);
});

for (const responseType of ['json', 'blob']) {
  test(`body read failure reports once for ${responseType}`, async () => {
    const { frame, events } = setup();
    let calls = 0;
    const client = make(frame, async () => {
      calls++;
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.error(new Error('private-read-error'));
          },
        }),
      );
    });
    await assert.rejects(
      client.request({
        method: 'GET',
        path: '/api/v1/content/download',
        responseType,
      }),
    );
    assert.equal(calls, 1);
    assert.equal(events.length, 1);
    assert.equal(events[0].name, 'client_request_fail');
    assert.equal(events[0].params.errorKind, 'response-read');
    assert.equal(events[0].params.phase, 'read-body');
    assert.equal(events[0].params.responseType, responseType);
  });
}

test('in-flight requests survive disabling with no late report or replay', async () => {
  const { frame, controller, events } = setup();
  let complete;
  const client = make(
    frame,
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const pending = client.request({
    method: 'GET',
    path: '/api/v1/system/status',
  });
  while (!complete) await Promise.resolve();
  controller.configure(false);
  complete(ok('done'));
  assert.equal(await pending, 'done');
  controller.configure({ onEvent: (event) => events.push(event) });
  assert.equal(events.length, 0);
});

test('only transformed diagnostics are delivered, with independent body limits', async () => {
  const { frame, events } = setup('FileBrowser', {
    onEvent: (event) => events.push(event),
    diagnostics: {
      transform: (input) => {
        assert.equal(input.requestBody.secret, 'private-value');
        return {
          message: 'redacted',
          requestBody: { safe: true },
          responseBody: 'x'.repeat(5000),
          stack: 'x'.repeat(17000),
        };
      },
    },
  });
  const client = make(frame, async () =>
    Response.json({ status: 'error', error: { message: 'private-response' } }),
  );
  await assert.rejects(
    client.request({
      method: 'POST',
      path: '/api/v1/fs/ls',
      body: { secret: 'private-value' },
    }),
  );
  assert.deepEqual(events[0].diagnostics.requestBody, { safe: true });
  assert.equal(events[0].diagnostics.responseBody, undefined);
  assert.equal(events[0].diagnostics.stack, undefined);
  assert.equal(JSON.stringify(events).includes('private'), false);
});

test('a throwing redactor and receiver preserve the original successful or failed result', async () => {
  const { frame } = setup('Console', {
    onEvent() {
      throw new Error('receiver');
    },
    diagnostics: {
      transform() {
        throw new Error('redactor');
      },
    },
  });
  const good = make(frame, async () => ok(7));
  assert.equal(
    await good.request({ method: 'GET', path: '/api/v1/system/status' }),
    7,
  );
  const bad = make(frame, async () => new Response('private', { status: 503 }));
  await assert.rejects(
    bad.request({ method: 'GET', path: '/private' }),
    (error) => error.statusCode === 503,
  );
});

test('directory normalization failures report their workflow after successful HTTP reading', async () => {
  const { frame, events } = setup();
  const client = runtime.bindClient(
    runtime.createOvClient({ fetch: async () => ok([null]) }),
    frame,
  );
  await assert.rejects(
    runtime.loadPage(
      client,
      'viking://resources/example',
      0,
      new AbortController().signal,
    ),
    /Invalid directory response/,
  );
  const failure = events.find(
    (event) => event.name === 'file_browser_directory_load_fail',
  );
  assert.equal(failure.params.errorKind, 'protocol');
  assert.equal(failure.params.code, 'DATA_RESPONSE_INVALID');
  assert.ok(
    failure.params.traceSnapshot.spans.some(
      (span) => span.stage === 'client-request' && span.status === 'ready',
    ),
  );
});
