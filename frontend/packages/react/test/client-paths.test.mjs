import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  OV_PATHS,
  OV_PATH_BUILDERS,
} from '@openviking/react/client';

/** 记录每次调用，并按 handler 返回预设响应。 */
function fakeFetch(handler) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  impl.calls = calls;
  return impl;
}

const ok = () =>
  new Response(JSON.stringify({ status: 'ok', result: null }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

test('every static path starts with a slash and carries no host', () => {
  for (const [name, path] of Object.entries(OV_PATHS)) {
    assert.ok(path.startsWith('/'), `${name} must start with a slash`);
    assert.ok(!path.includes('://'), `${name} must not carry a host`);
    assert.ok(!path.endsWith('/'), `${name} must not end with a slash`);
  }
});

test('every static path belongs to a known API family', () => {
  for (const [name, path] of Object.entries(OV_PATHS)) {
    assert.match(
      path,
      /^\/(api|bot)\/v1\//,
      `${name} must live under /api/v1 or /bot/v1`,
    );
  }
});

test('static paths are unique', () => {
  const values = Object.values(OV_PATHS);
  assert.equal(new Set(values).size, values.length);
});

test('a path builder encodes its parameter', () => {
  assert.equal(OV_PATH_BUILDERS.session('abc'), `${OV_PATHS.sessions}/abc`);
  assert.equal(
    OV_PATH_BUILDERS.session('a/b c'),
    `${OV_PATHS.sessions}/a%2Fb%20c`,
  );
  assert.equal(
    OV_PATH_BUILDERS.sessionMessages('a/b'),
    `${OV_PATHS.sessions}/a%2Fb/messages`,
  );
});

test('a builder result goes through the client unchanged', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await client.request({
    method: 'POST',
    path: OV_PATH_BUILDERS.sessionMessages('s 1'),
    body: { role: 'user', content: 'hi' },
  });

  assert.equal(
    fetch.calls[0].url,
    `https://ov.test${OV_PATHS.sessions}/s%201/messages`,
  );
});
