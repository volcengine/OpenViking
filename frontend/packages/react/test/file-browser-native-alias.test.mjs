import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  openAction,
  workflow,
  act,
  ok,
  settle,
} from './helpers/preview-native-dom.mjs';

test('a configuration dialog keeps its resolved target and success state while an alias refreshes', async () => {
  const uri = 'viking://~/resources/wiki';
  const home = 'viking://user/alice';
  const to = `${home}/resources/wiki`;
  const config = {
    to,
    from: ['viking://resources/source'],
    skill: `${home}/skills/compile`,
    instruction: 'instruction',
    auto_update: false,
    update_schedule: null,
  };
  const fetch = fixture(
    uri,
    true,
    (call) => {
      if (call.path.endsWith('/fs/stat'))
        return ok({ uri: call.uri === 'viking://~' ? home : to, isDir: true });
      if (call.path.endsWith('/fs/attrs'))
        return ok({ uri: to, attrs: { tags: ['compile_target=true'] } });
      if (call.path.endsWith('/compile/config')) return ok(config);
      if (call.path.endsWith('/compile/config/update')) return ok(call.body);
    },
    [{ to }],
  );
  const view = await mount(tree(fetch, uri, { rootUri: 'viking://~' }));
  try {
    await openAction('Compile configuration');
    assert.equal(
      fetch.calls.find((call) => call.path.endsWith('/compile/config')).query
        .to,
      to,
    );
    await act(async () =>
      workflow()
        .querySelector('form')
        .dispatchEvent(
          new window.Event('submit', { bubbles: true, cancelable: true }),
        ),
    );
    await settle(8);
    assert.equal(
      fetch.calls.filter((call) => call.path.endsWith('/compile/config'))
        .length,
      1,
    );
    assert.match(
      workflow().querySelector('[role="status"]').textContent,
      /Configuration saved/,
    );
    assert.ok(
      fetch.calls.filter((call) => call.path.endsWith('/compile/configs'))
        .length > 1,
    );
  } finally {
    await view.unmount();
  }
});
