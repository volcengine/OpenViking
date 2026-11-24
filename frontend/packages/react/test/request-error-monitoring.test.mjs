import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { runtime as r } from './helpers/monitoring.mjs';
import { mount } from './helpers/module-dom.mjs';
import { notificationError } from './helpers/feedback-dom.mjs';

for (const local of [false, true]) {
  test(`request monitoring retains failure delivery with ${local ? 'local' : 'automatic'} error feedback`, async () => {
    const events = [];
    let rejected;
    const View = r.withMonitoring(() => {
      const client = r.useOvClient(local ? 'local' : 'default');
      return h(
        'button',
        {
          onClick: async () => {
            try {
              await client.request({ method: 'POST', path: '/example' });
            } catch (error) {
              rejected = error;
            }
          },
        },
        'Request',
      );
    }, 'FilePreview');
    const view = await mount(
      h(
        r.OpenVikingProvider,
        {
          monitoring: { onEvent: (event) => events.push(event) },
          fetch: async () =>
            Response.json(
              {
                status: 'error',
                error: { code: 'DEMO_ERROR', message: 'Backend reason' },
              },
              { status: 503, headers: { 'x-request-id': 'request-7' } },
            ),
        },
        h(View),
      ),
    );
    try {
      await act(async () => view.container.querySelector('button').click());
      assert.equal(rejected.message, 'Backend reason');
      const failures = events.filter(
        (event) => event.name === 'client_request_fail',
      );
      assert.equal(failures.length, 1);
      assert.equal(failures[0].params.requestId, 'request-7');
      assert.equal(failures[0].params.httpStatus, 503);
      assert.equal(Boolean(notificationError()), !local);
      if (!local)
        assert.match(notificationError().textContent, /Backend reason/);
    } finally {
      await view.unmount();
    }
  });
}
