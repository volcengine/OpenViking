import assert from 'node:assert/strict';
import { createElement as h } from 'react';
import test from 'node:test';
import { mount, ok } from './helpers/module-dom.mjs';

const { OpenVikingProvider, useOvClient } = await import(
  `${process.env.OV_UI_TEST_HOOKS}/provider.js`
);

test('nested providers inherit, override and explicitly clear request settings', async () => {
  const calls = [];
  const clients = new Map();
  const transport =
    (origin) =>
    async (url, { credentials, headers }) => {
      calls.push({
        origin,
        url: String(url),
        credentials,
        apiKey: headers['x-api-key'],
        account: headers['x-openviking-account'],
        user: headers['x-openviking-user'],
      });
      return ok([]);
    };

  function Probe({ name }) {
    clients.set(name, useOvClient());
    return h('span', null, name);
  }

  const view = await mount(
    h(
      OpenVikingProvider,
      {
        baseURL: 'https://ov.test/gateway',
        apiKey: 'parent-key',
        accountId: 'acct-1',
        credentials: 'omit',
        fetch: transport('parent'),
      },
      h(
        OpenVikingProvider,
        { userId: 'alice' },
        h(Probe, { name: 'inherited' }),
      ),
      h(
        OpenVikingProvider,
        { apiKey: '', baseURL: '', credentials: 'include' },
        h(Probe, { name: 'cleared' }),
      ),
      h(
        OpenVikingProvider,
        { baseURL: 'https://other.test', fetch: transport('child') },
        h(Probe, { name: 'replaced' }),
      ),
    ),
  );

  try {
    assert.equal(view.container.textContent, 'inheritedclearedreplaced');
    assert.deepEqual(calls, []);
    for (const client of clients.values()) {
      await client.request({ method: 'GET', path: '/api/v1/sessions' });
    }
    assert.deepEqual(calls, [
      {
        origin: 'parent',
        url: 'https://ov.test/gateway/api/v1/sessions',
        credentials: 'omit',
        apiKey: 'parent-key',
        account: 'acct-1',
        user: 'alice',
      },
      {
        origin: 'parent',
        url: '/api/v1/sessions',
        credentials: 'include',
        apiKey: undefined,
        account: 'acct-1',
        user: undefined,
      },
      {
        origin: 'child',
        url: 'https://other.test/api/v1/sessions',
        credentials: 'omit',
        apiKey: 'parent-key',
        account: 'acct-1',
        user: undefined,
      },
    ]);
  } finally {
    await view.unmount();
  }
});
