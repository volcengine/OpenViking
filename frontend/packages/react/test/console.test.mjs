import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, type, click } from './helpers/tui-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

test('Console uses native requests, validates before sending and resubmits after failure', async () => {
  const calls = [];
  let failure = false;
  const host = await mount(
    async (url, init) => {
      calls.push({ url: new URL(url), init });
      return failure
        ? Response.json(
            {
              status: 'error',
              error: { code: 'DENIED', message: 'No access' },
            },
            { status: 403, headers: { 'x-request-id': 'demo-request' } },
          )
        : ok('example content');
    },
    { requestOptions: { userId: 'local' } },
    { accountId: 'account', userId: 'inherited' },
  );
  try {
    assert.equal(calls.length, 0);
    await host.execute(
      'ov write "viking://resources/a b.md" "hello world" --mode create',
    );
    assert.equal(calls[0].url.pathname, '/gateway/api/v1/content/write');
    assert.equal(calls[0].init.headers['x-openviking-user'], 'local');
    assert.equal(calls[0].init.headers['x-openviking-account'], 'account');
    assert.deepEqual(JSON.parse(calls[0].init.body), {
      uri: 'viking://resources/a b.md',
      content: 'hello world',
      mode: 'create',
    });
    for (const input of [
      'ov rm file:///tmp/test',
      'ov read',
      'ov ls --unknown',
      'ov ls --limit 0',
      'ov ls --limit 2 --limit 3',
      'ov health extra',
      'ov read "unterminated',
      'ov status; other',
      'ov clear extra',
    ]) {
      await host.execute(input);
      assert.equal(host.records.at(-1).dataset.status, 'error', input);
    }
    assert.equal(calls.length, 1);
    failure = true;
    await host.execute('/read viking://resources/demo');
    assert.match(host.records.at(-1).textContent, /DENIED: No access/);
    assert.equal(host.container.querySelector('.ov-console__error'), null);
    assert.equal(host.button('Retry command'), undefined);
    failure = false;
    await host.execute('/read viking://resources/demo');
    assert.equal(host.records.at(-1).dataset.status, 'success');
    assert.match(host.records.at(-1).textContent, /example content/);
    await host.execute('ov help');
    assert.match(host.records.at(-1).textContent, /Available commands/);
    await host.execute('clear');
    assert.equal(host.records.length, 0);
  } finally {
    await host.dispose();
  }
});

test('Console isolates instances and ignores late responses after cancellation, scope changes and unmount', async () => {
  const pending = [];
  const transport = (url, init) =>
    new Promise((resolve) => pending.push({ resolve, signal: init.signal }));
  const parent = new AbortController();
  const host = await mount(transport, {}, { signal: parent.signal });
  const other = await mount(async () => ok('other'));
  try {
    await host.execute('ov health');
    assert.equal(pending.length, 1);
    assert.equal(host.input.disabled, true);
    await act(() => parent.abort());
    assert.equal(pending[0].signal.aborted, true);
    assert.equal(host.input.disabled, false);
    await act(() => pending[0].resolve(ok({ stale: true })));
    assert.equal(host.records[0].dataset.status, 'error');
    await host.render({
      scopeKey: 'new',
      requestOptions: { signal: new AbortController().signal },
    });
    assert.equal(host.records.length, 0);
    await host.execute('ov status');
    await host.render({ scopeKey: 'next' }, {});
    assert.equal(pending[1].signal.aborted, true);
    assert.equal(host.input.value, '');
    await act(() => pending[1].resolve(ok({ stale: true })));
    assert.equal(host.records.length, 0);
    await other.execute('ov health');
    assert.equal(other.records.length, 1);
    await host.execute('ov health');
    assert.equal(host.button('Cancel request'), undefined);
    await host.press('New session');
    assert.equal(pending[2].signal.aborted, true);
    assert.equal(host.records.length, 0);
    await act(() => pending[2].resolve(ok({ stale: true })));
    assert.doesNotMatch(host.text, /stale/);
    await host.execute('ov health');
    await host.dispose();
    assert.equal(pending[3].signal.aborted, true);
    await act(() => pending[3].resolve(ok({ stale: true })));
  } finally {
    await host.dispose();
    await other.dispose();
  }
});

test('Console reports controlled mode intent and supports quick actions, keyboard submission and dated history recall', async (t) => {
  t.mock.method(Date, 'now', () => new Date(2026, 10, 9, 0, 4).getTime());
  const changes = [];
  const host = await mount(async () => ok({ state: 'ready' }), {
    mode: 'command',
    onModeChange: (mode) => changes.push(mode),
  });
  try {
    await type(host.input, 'ov health');
    await host.press('VikingBot');
    assert.deepEqual(changes, ['conversation']);
    assert.equal(host.button('Terminal').getAttribute('aria-pressed'), 'true');
    assert.equal(host.input.value, 'ov health');
    await click(host.container.querySelector('.ov-tui__empty-action'));
    assert.equal(host.input.value, 'ov help ');
    assert.equal(document.activeElement, host.input);
    await host.execute('ov health');
    assert.match(host.text, /ready/);
    assert.equal(host.input.value, '');
    await host.press('Command history');
    assert.equal(
      document.querySelector('.ov-console__history-time').textContent,
      '2026-11-09 00:04',
    );
    await click(document.querySelector('.ov-console__history-content button'));
    assert.equal(host.input.value, 'ov health');
    assert.equal(document.activeElement, host.input);
    assert.equal(
      host.button('Command history').getAttribute('aria-expanded'),
      'false',
    );
    assert.equal(host.records.length, 1);
    await host.press('New session');
    assert.equal(host.input.value, '');
    assert.equal(host.records.length, 0);
    assert.equal(host.button('New session').disabled, false);
    await host.press('New session');
    assert.equal(document.activeElement, host.input);
  } finally {
    await host.dispose();
  }
});

test('Console exposes the terminal empty state and lets the host replace its quick commands', async () => {
  const host = await mount(async () => ok({ state: 'ready' }));
  try {
    const names = () =>
      [...host.container.querySelectorAll('.ov-tui__empty-name')].map(
        (element) => element.textContent,
      );
    assert.deepEqual(names(), ['help', 'ls', 'find', 'read', 'status']);
    assert.equal(
      host.container.querySelector('.ov-tui__empty-title').textContent,
      'OpenViking CLI',
    );

    await host.render({ quickCommands: ['status', 'missing', 'help'] });
    assert.deepEqual(names(), ['status', 'help']);

    await host.render({ quickCommands: [] });
    assert.deepEqual(names(), []);
    assert.equal(host.container.querySelector('.ov-tui__empty'), null);
  } finally {
    await host.dispose();
  }
});
