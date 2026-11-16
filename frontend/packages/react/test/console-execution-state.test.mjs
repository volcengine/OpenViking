import assert from 'node:assert/strict';
import test from 'node:test';
import { act, key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

for (const outcome of ['success', 'error']) {
  test(`Console shows pending progress without an unavailable warning and unlocks after ${outcome}`, async () => {
    let finish;
    let requests = 0;
    const host = await mount(
      () => {
        requests++;
        return new Promise((resolve) => (finish = resolve));
      },
      { modes: ['command'] },
    );
    try {
      await host.execute('ov health');
      assert.equal(host.input.disabled, true);
      assert.equal(host.records[0].dataset.status, 'pending');
      assert.equal(
        host.records[0]
          .querySelector('[role="img"]')
          .getAttribute('aria-label'),
        'Running',
      );
      assert.equal(
        host.container.querySelector('.ov-command-console__unavailable'),
        null,
      );
      assert.equal(
        host.container.querySelector('[aria-busy="true"]') !== null,
        true,
      );
      await key(host.input, 'Enter', { ctrlKey: true });
      assert.equal(requests, 1);

      await host.render({ modes: ['command'], locale: 'zh-CN' });
      assert.equal(
        host.records[0]
          .querySelector('[role="img"]')
          .getAttribute('aria-label'),
        '执行中',
      );
      assert.equal(
        host.container.querySelector('.ov-command-console__unavailable'),
        null,
      );
      assert.equal(host.input.disabled, true);
      assert.equal(requests, 1);
      await act(() =>
        finish(
          outcome === 'success'
            ? ok({ status: 'healthy' })
            : Response.json(
                {
                  status: 'error',
                  error: { code: 'DENIED', message: 'No access' },
                },
                { status: 403 },
              ),
        ),
      );
      assert.equal(host.input.disabled, false);
      assert.equal(host.records[0].dataset.status, outcome);
      assert.equal(host.container.querySelector('[aria-busy="true"]'), null);
      if (outcome === 'error')
        assert.match(host.records[0].textContent, /DENIED: No access/);
      await host.execute('ov health');
      assert.equal(requests, 2);
      await act(() => finish(ok({ status: 'healthy' })));
      assert.equal(host.records[1].dataset.status, 'success');
    } finally {
      await host.dispose();
    }
  });
}
