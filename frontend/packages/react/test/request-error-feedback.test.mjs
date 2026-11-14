import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode, useState } from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { mount } from './helpers/module-dom.mjs';
import { notificationError } from './helpers/feedback-dom.mjs';
const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { OpenVikingProvider, useOvClient } = await load('provider');
const { ErrorNotification } = await load('error-feedback');
const { Modal } = await load('modal');
const spec = { method: 'POST', path: '/example' };
let rejected;
let clientIdentity;
function Requester({ local = false, signal, inModal = false }) {
  const client = useOvClient(local ? 'local' : 'default');
  clientIdentity = client;
  const [error, setError] = useState();
  const content = h(
    'button',
    {
      onClick: async () => {
        try {
          await client.request({ ...spec, options: { signal } });
        } catch (reason) {
          rejected = reason;
          setError(reason);
        }
      },
    },
    'Request',
  );
  return h(
    'section',
    null,
    h('p', null, 'Normal content'),
    inModal
      ? h(
          Modal,
          {
            open: true,
            title: 'Edit',
            triggerRef: { current: null },
            onClose() {},
          },
          content,
        )
      : content,
    error &&
      h(
        ErrorNotification,
        { title: 'Existing failure', error, identity: error },
        error.message,
      ),
  );
}
const response = () =>
  new Response(
    JSON.stringify({
      status: 'error',
      error: { code: 'DEMO_ERROR', message: 'Backend reason' },
    }),
    {
      status: 503,
      headers: {
        'content-type': 'application/json',
        'x-request-id': 'request-copy-42',
      },
    },
  );
const click = async () =>
  act(async () =>
    [...document.querySelectorAll('button')]
      .find((node) => node.textContent === 'Request')
      .click(),
  );
const tree = (props = {}, children = h(Requester)) =>
  h(
    StrictMode,
    null,
    h(OpenVikingProvider, { fetch: response, ...props }, children),
  );

test('Provider feedback needs no per-call presentation and does not duplicate existing error views', async () => {
  const copied = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (text) => copied.push(text) },
  });
  const view = await mount(tree({ locale: 'zh-CN' }));
  try {
    await click();
    assert.equal(rejected.message, 'Backend reason');
    assert.equal(
      document.querySelectorAll('.ov-notification--error').length,
      1,
    );
    assert.match(notificationError().textContent, /请求失败/);
    assert.equal(
      view.container.querySelector('p').textContent,
      'Normal content',
    );
    await act(async () =>
      [...document.querySelectorAll('button')]
        .find((node) => node.textContent === '复制错误信息')
        .click(),
    );
    assert.match(copied[0], /Backend reason/);
    assert.match(copied[0], /DEMO_ERROR/);
    assert.match(copied[0], /request-copy-42/);
    const before = clientIdentity;
    await view.render(tree({ locale: 'en-US' }));
    assert.equal(clientIdentity, before);
    assert.match(notificationError().textContent, /Request failed/);
    await act(async () =>
      notificationError().querySelector('.ov-notification__close').click(),
    );
    await view.render(tree({ locale: 'zh-CN' }));
    assert.equal(Boolean(notificationError()), false);
  } finally {
    await view.unmount();
  }
});

test('a local error owner keeps its presentation and cancellation stays silent', async () => {
  const controller = new AbortController();
  const view = await mount(tree({}, h(Requester, { local: true })));
  try {
    await click();
    assert.match(notificationError().textContent, /Existing failure/);
    assert.doesNotMatch(notificationError().textContent, /Request failed/);
  } finally {
    await view.unmount();
  }
  const aborted = await mount(
    tree({}, h(Requester, { signal: controller.signal })),
  );
  try {
    await click();
    assert.ok(notificationError());
    await act(async () => controller.abort());
    assert.equal(Boolean(notificationError()), false);
  } finally {
    await aborted.unmount();
  }
});

test('an outer request handler contributes feedback to the open Modal focus scope', async () => {
  const view = await mount(tree({}, h(Requester, { inModal: true })));
  try {
    await click();
    const error = notificationError();
    assert.ok(error);
    assert.match(error.textContent, /Backend reason/);
    assert.equal(
      document.querySelectorAll('.ov-notification--error').length,
      1,
    );
  } finally {
    await view.unmount();
  }
});
