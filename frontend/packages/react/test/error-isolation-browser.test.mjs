import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  fixture,
  tree,
  mount,
  h,
  target,
  settle,
} from './helpers/preview-native-dom.mjs';

function Crash() {
  throw new Error('preview header failed');
}

test('FileBrowser keeps tree, current selection and request state when a preview slot fails', async (t) => {
  t.mock.method(console, 'error', () => {});
  const fetch = fixture(target, false);
  const host = await mount(tree(fetch, target));
  try {
    await settle(5);
    const fileTree = host.container.querySelector('.ov-file-tree');
    const calls = fetch.calls.length;
    await host.render(
      tree(fetch, target, { previewSlots: { extraActions: h(Crash) } }),
    );
    assert.ok(host.container.querySelector('.ov-error-boundary'));
    assert.equal(host.container.querySelector('.ov-file-tree'), fileTree);
    assert.equal(
      fetch.calls.length,
      calls,
      'preview failure does not restart browser requests',
    );
    const selected = host.container.querySelector('[aria-selected="true"]');
    assert.ok(selected);
    await host.render(tree(fetch, target));
    await import('react').then(({ act }) =>
      act(() =>
        host.container.querySelector('.ov-error-boundary button').click(),
      ),
    );
    assert.equal(host.container.querySelector('.ov-error-boundary'), null);
    assert.equal(host.container.querySelector('.ov-file-tree'), fileTree);
    assert.equal(fetch.calls.length, calls);
  } finally {
    await host.unmount();
  }
});

test('FileBrowser passes its error slot to a failed preview while preserving the tree and requests', async (t) => {
  t.mock.method(console, 'error', () => {});
  const fetch = fixture(target, false);
  const errorFallback = h(
    'aside',
    { className: 'browser-host-error', style: { padding: 17 } },
    'Host preview failure',
  );
  const view = (fallback, broken = true) =>
    tree(fetch, target, {
      slots: { errorFallback: fallback },
      previewSlots: { extraActions: broken ? h(Crash) : null },
    });
  const host = await mount(view(errorFallback, false));
  try {
    await settle(5);
    const fileTree = host.container.querySelector('.ov-file-tree');
    const calls = fetch.calls.length;
    await host.render(view(errorFallback));
    assert.match(host.container.textContent, /Host preview failure/);
    assert.equal(
      host.container.querySelector('.browser-host-error').style.padding,
      '17px',
    );
    assert.equal(host.container.querySelector('.ov-file-tree'), fileTree);
    assert.equal(fetch.calls.length, calls);
    assert.equal(host.container.querySelector('[slots]'), null);
    await host.render(view(undefined));
    assert.match(
      host.container.textContent,
      /Tab content could not be displayed/,
    );
    await host.render(view(undefined, false));
    await import('react').then(({ act }) =>
      act(() =>
        host.container.querySelector('.ov-error-boundary button').click(),
      ),
    );
    assert.equal(host.container.querySelector('.ov-file-tree'), fileTree);
    assert.equal(fetch.calls.length, calls);
  } finally {
    await host.unmount();
  }
});
