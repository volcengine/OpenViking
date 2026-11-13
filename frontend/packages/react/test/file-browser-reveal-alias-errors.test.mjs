import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, createRef } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
import { command } from './helpers/file-browser-reveal.mjs';

const root = 'viking://~/resources';
const alias = `${root}/a.png`;
const canonical = 'viking://user/default/resources/a.png';
const isHomeLookup = (call) =>
  call.path === '/api/v1/fs/stat' && call.uri === 'viking://~';
const home = () => ok({ uri: 'viking://user/default', isDir: true });
const failure = (status, code) =>
  Response.json(
    { status: 'error', error: { code, message: 'Alias lookup failed' } },
    { status, headers: { 'x-request-id': 'lookup-1' } },
  );

async function browser(lookup, requestOptions) {
  const fetch = recordingFetch((call) => {
    if (isHomeLookup(call)) return lookup(call.init.signal);
    if (call.path === '/api/v1/fs/ls') return ok([{ uri: alias }]);
    if (call.path === '/api/v1/fs/stat')
      return ok({ uri: call.uri, isDir: false });
    return ok('');
  });
  const handle = createRef();
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch },
      h(FileBrowser, {
        ref: handle,
        rootUri: root,
        dashboardKinds: [],
        requestOptions,
      }),
    ),
  );
  return {
    view,
    handle,
    lookups: () => fetch.calls.filter(isHomeLookup).length,
  };
}

const capture = (handle) =>
  handle.current.openResource(canonical).then(
    (result) => ({ result }),
    (error) => ({ error }),
  );

async function openCanonical(handle) {
  let result;
  await act(async () => {
    result = capture(handle);
  });
  await settle();
  return result;
}

for (const [status, code] of [
  [500, 'INTERNAL_ERROR'],
  [401, 'UNAUTHENTICATED'],
  [403, 'PERMISSION_DENIED'],
  [404, 'NOT_FOUND'],
  [504, 'DEADLINE_EXCEEDED'],
]) {
  test(`required home lookup preserves HTTP ${status} and allows retry`, async () => {
    let fail = true;
    const { view, handle } = await browser(() =>
      fail ? failure(status, code) : home(),
    );
    try {
      const { error } = await openCanonical(handle);
      assert.equal(error.name, 'OvClientError');
      assert.equal(error.statusCode, status);
      assert.equal(error.code, code);
      assert.equal(error.message, 'Alias lookup failed');
      assert.equal(error.requestId, 'lookup-1');
      fail = false;
      assert.deepEqual(await openCanonical(handle), { result: true });
    } finally {
      await view.unmount();
    }
  });
}

test('home lookup preserves the client network error and its cause', async () => {
  const cause = new TypeError('Connection unavailable');
  const { view, handle } = await browser(() => {
    throw cause;
  });
  try {
    const { error } = await openCanonical(handle);
    assert.equal(error.code, 'NETWORK_ERROR');
    assert.equal(error.cause, cause);
  } finally {
    await view.unmount();
  }
});

for (const [label, lookup] of [
  ['unsupported URI', () => failure(400, 'UNSUPPORTED_URI')],
  ['unimplemented lookup', () => failure(501, 'UNIMPLEMENTED')],
  ['unchanged alias', () => ok({ uri: 'viking://~', isDir: true })],
]) {
  test(`a gateway reporting ${label} falls back to matching raw URIs`, async () => {
    const { view, handle, lookups } = await browser(lookup);
    try {
      assert.deepEqual(await openCanonical(handle), { result: false });
      assert.equal(await command(handle, alias), true);
      assert.equal(lookups(), 1);
    } finally {
      await view.unmount();
    }
  });
}

test('same-spelling home paths reveal without requesting home identity', async () => {
  const { view, handle, lookups } = await browser(() =>
    failure(500, 'INTERNAL_ERROR'),
  );
  try {
    assert.equal(await command(handle, alias), true);
    assert.equal(lookups(), 0);
  } finally {
    await view.unmount();
  }
});

test('a malformed stat does not count as an unsupported lookup', async () => {
  const { view, handle } = await browser(() => ok({ isDir: true }));
  try {
    assert.match(
      (await openCanonical(handle)).error.message,
      /Invalid home directory stat/,
    );
  } finally {
    await view.unmount();
  }
});

for (const mode of ['superseded', 'caller cancelled', 'caller timeout']) {
  test(`a ${mode} home lookup does not lose its termination semantics`, async () => {
    const controller = new AbortController();
    let lookupSignal;
    const { view, handle } = await browser(
      (signal) => {
        lookupSignal = signal;
        return new Promise((_, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason), {
            once: true,
          });
        });
      },
      { signal: controller.signal },
    );
    try {
      let pending;
      await act(async () => {
        pending = capture(handle);
      });
      await settle();
      assert.ok(lookupSignal);
      if (mode === 'superseded') {
        assert.equal(await command(handle, alias), true);
        assert.deepEqual(await pending, { result: false });
      } else if (mode === 'caller timeout') {
        const timeout = new DOMException(
          'Navigation timed out',
          'TimeoutError',
        );
        await act(async () => controller.abort(timeout));
        assert.equal((await pending).error, timeout);
      } else {
        await act(async () => controller.abort());
        assert.deepEqual(await pending, { result: false });
      }
      assert.equal(lookupSignal.aborted, true);
    } finally {
      await view.unmount();
    }
  });
}
