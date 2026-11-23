import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  fixture,
  tree,
  mount,
  target,
  openAction,
  workflow,
  click,
  button,
  ok,
} from './helpers/preview-native-dom.mjs';

const config = (name) => ({
  compile_name: name,
  to: target,
  from: [`viking://resources/${name}`],
  skill: 'viking://system/compile/example-skills/llm-wiki',
  instruction: 'Keep these instructions',
  args: { model_name: 'existing-model', max_rounds: 8 },
  auto_update: true,
  update_schedule: { frequency: 'weekly', hour: 9, minute: 30, weekday: 2 },
});
function backend(configs) {
  return fixture(target, true, (call) => {
    if (call.path.endsWith('/fs/attrs') && call.uri === target)
      return ok({ attrs: { tags: ['compile_target=true'] } });
    if (call.path.endsWith('/compile/configs')) {
      return ok({
        configs: configs.slice(
          call.query.cursor ? 1 : 0,
          call.query.cursor ? 2 : 1,
        ),
        next_cursor: !call.query.cursor && configs.length > 1 ? 'alpha' : null,
      });
    }
    if (call.path.endsWith('/compile/config')) {
      assert.equal(call.query.to, undefined);
      return ok(
        configs.find((item) => item.compile_name === call.query.compile_name),
      );
    }
    if (call.path.endsWith('/compile/config/update')) return ok(call.body);
  });
}
async function select(name) {
  await click(
    [...workflow().querySelectorAll('[role="tab"]')].find(
      (node) => node.textContent === name,
    ),
  );
}

test('named configuration tabs select the first item, query by name and preserve args on update', async () => {
  const fetch = backend([config('alpha'), config('beta')]);
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Compile configuration');
    assert.equal(fetch.countOf('/api/v1/compile/config'), 1);
    assert.match(workflow().textContent, /resources\/alpha/);
    assert.equal(
      workflow().querySelector('[role="tab"][aria-selected="true"]')
        .textContent,
      'alpha',
    );
    const first = workflow().querySelector(
      '[role="tab"][aria-selected="true"]',
    );
    await act(async () =>
      first.dispatchEvent(
        new window.KeyboardEvent('keydown', {
          key: 'ArrowRight',
          bubbles: true,
        }),
      ),
    );
    assert.equal(document.activeElement.textContent, 'beta');
    assert.equal(fetch.countOf('/api/v1/compile/config'), 1);
    assert.equal(first.getAttribute('aria-selected'), 'true');
    await select('beta');
    assert.match(workflow().textContent, /resources\/beta/);
    await click(document.querySelector('[aria-label="Update schedule"]'));
    const edit = document.querySelector('.ov-compile-config [role="dialog"]');
    await click(button('Save', edit));
    const saved = fetch.calls.find((call) =>
      call.path.endsWith('/compile/config/update'),
    );
    assert.deepEqual(saved.body, config('beta'));
    await select('alpha');
    assert.match(workflow().textContent, /resources\/alpha/);
    const reads = fetch.calls.filter((call) =>
      call.path.endsWith('/compile/config'),
    );
    assert.deepEqual(
      reads.map((call) => call.query),
      [
        { compile_name: 'alpha' },
        { compile_name: 'beta' },
        { compile_name: 'alpha' },
      ],
    );
  } finally {
    await view.unmount();
  }
});

test('a single named configuration opens directly without showing tabs', async () => {
  const fetch = backend([config('alpha')]);
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Compile configuration');
    assert.match(workflow().textContent, /resources\/alpha/);
    assert.equal(workflow().querySelector('[role="tablist"]'), null);
    assert.equal(
      fetch.calls.find((call) => call.path.endsWith('/compile/config')).query
        .compile_name,
      'alpha',
    );
  } finally {
    await view.unmount();
  }
});

test('another user’s tagged Wiki remains visible with no configurations in the current user scope', async () => {
  const fetch = backend([]);
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Compile configuration');
    assert.match(
      workflow().textContent,
      /No compilation configuration is available/,
    );
    assert.equal(fetch.countOf('/api/v1/compile/config'), 0);
    assert.equal(fetch.countOf('/api/v1/compile/config/update'), 0);
  } finally {
    await view.unmount();
  }
});
