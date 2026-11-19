import assert from 'node:assert/strict';
import test from 'node:test';
import { act, key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const resultBody = '.ov-command-console__result-body';

for (const outcome of ['success', 'error']) {
  test(`Console shows pending progress without an unavailable warning and unlocks after ${outcome}`, async () => {
    let finish;
    let signal;
    let requests = 0;
    const host = await mount(
      (_url, init) => {
        requests++;
        signal = init.signal;
        return new Promise((resolve) => (finish = resolve));
      },
      { modes: ['command'], commandOptions: { allowAdminCommands: true } },
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

      await host.render({
        modes: ['command'],
        locale: 'zh-CN',
        commandOptions: { allowAdminCommands: true },
      });
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
      assert.equal(signal.aborted, false);
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
        assert.equal(
          host.records[0].querySelector(resultBody).textContent,
          'No access',
        );
      await host.execute('ov health');
      assert.equal(requests, 2);
      await act(() => finish(ok({ status: 'healthy' })));
      assert.equal(host.records[1].dataset.status, 'success');
      await host.render({
        modes: ['command'],
        commandOptions: { allowAdminCommands: false },
      });
      assert.equal(host.records.length, 2);
      await host.execute('ov backup sample.ovpack');
      assert.match(host.records[2].textContent, /unavailable/i);
      assert.equal(requests, 2);
    } finally {
      await host.dispose();
    }
  });
}

test('Console shows the transport message when add-resource fails', async () => {
  const host = await mount(
    async () => {
      throw new TypeError('NetworkError');
    },
    { modes: ['command'] },
  );
  try {
    await host.execute(
      'ov add-resource https://example.com/notes.md --wait=true',
    );
    const [record] = host.records;
    assert.equal(record.dataset.status, 'error');
    assert.equal(record.querySelector(resultBody).textContent, 'NetworkError');
  } finally {
    await host.dispose();
  }
});

test('Console falls back to the HTTP error code when the response has no message', async () => {
  const host = await mount(async () => new Response(null, { status: 502 }), {
    modes: ['command'],
  });
  try {
    await host.execute('ov read viking://resources/demo');
    const [record] = host.records;
    assert.equal(record.dataset.status, 'error');
    assert.equal(record.querySelector(resultBody).textContent, 'HTTP_502');
  } finally {
    await host.dispose();
  }
});
