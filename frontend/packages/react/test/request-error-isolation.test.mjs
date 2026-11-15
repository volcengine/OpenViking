import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { mount } from './helpers/module-dom.mjs';
import { notificationError } from './helpers/feedback-dom.mjs';

const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { OpenVikingProvider, useOvClient } = await load('provider');
const { NotificationScope } = await load('notification-scope');
const { Modal } = await load('modal');
const response = () =>
  new Response(
    JSON.stringify({
      status: 'error',
      error: { code: 'SERVER_ERROR', message: 'Account A failure' },
    }),
    { status: 503, headers: { 'content-type': 'application/json' } },
  );

function Requester() {
  const client = useOvClient();
  return h(
    'button',
    {
      onClick: () => {
        void client
          .request({ method: 'POST', path: '/example' })
          .catch(() => {});
      },
    },
    'Request A',
  );
}
const modal = h(
  Modal,
  {
    open: true,
    title: 'Account B',
    triggerRef: { current: null },
    onClose() {},
  },
  'B content',
);

for (const boundary of ['Provider', 'block']) {
  test(`${boundary} A failures stay outside unrelated B modal and survive its closure`, async () => {
    const render = (open) =>
      h(
        OpenVikingProvider,
        { locale: 'en-US', fetch: response },
        boundary === 'Provider'
          ? h(Requester)
          : h(NotificationScope, { isolateRequests: true }, h(Requester)),
        open &&
          (boundary === 'Provider'
            ? h(
                OpenVikingProvider,
                { locale: 'zh-CN', fetch: response, accountId: 'B' },
                modal,
              )
            : h(
                NotificationScope,
                { isolateRequests: true, locale: 'zh-CN' },
                modal,
              )),
      );
    const view = await mount(render(true));
    try {
      await act(async () => view.container.querySelector('button').click());
      assert.match(notificationError()?.textContent ?? '', /Request failed/);
      assert.doesNotMatch(notificationError().textContent, /请求失败/);
      await view.render(render(false));
      assert.match(notificationError()?.textContent ?? '', /Account A failure/);
      assert.match(notificationError().textContent, /Copy error details/);
    } finally {
      await view.unmount();
    }
  });
}
