import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  act,
  ok,
  settle,
} from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';

for (const [field, header] of [
  ['accountId', 'X-OpenViking-Account'],
  ['userId', 'X-OpenViking-User'],
  ['resourceId', 'V-Resource-Id'],
]) {
  test(`a late conflict index cannot mark a different ${field} scope`, async () => {
    const delayed = deferred();
    const fetch = fixture(
      target,
      true,
      (call) => {
        if (call.path.endsWith('/fs/ls') && call.query.recursive === 'false')
          return new Headers(call.init.headers).get(header) === 'before'
            ? delayed.promise
            : ok([]);
      },
      [{ to: target }],
    );
    const render = (value) =>
      field === 'resourceId'
        ? tree(fetch, null, { requestOptions: { resourceId: value } })
        : tree(fetch, null, {}, { [field]: value });
    const view = await mount(render('before'));
    try {
      const pending = fetch.calls.find(
        (call) => call.query.recursive === 'false',
      );
      assert.ok(pending);
      await view.render(render('after'));
      assert.equal(pending.init.signal.aborted, true);
      const count = fetch.calls.filter(
        (call) => call.query.recursive === 'false',
      ).length;
      assert.equal(count, 2);
      await act(async () =>
        delayed.resolve(
          await ok([
            { uri: `${target}/conflict.md`, tags: ['has_conflict=true'] },
          ]),
        ),
      );
      await settle();
      assert.equal(
        view.container.querySelector(
          '[aria-label="View pending Wiki conflicts"]',
        ),
        null,
      );
      assert.ok(
        view.container.querySelector('[aria-label="Open Wiki dashboard"]'),
      );
    } finally {
      await view.unmount();
    }
  });
}
