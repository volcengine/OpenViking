import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { act, createElement } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { ReferencePicker } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'reference-picker.js'))
);

test('VikingBot reference picker browses and navigates roots in context tree order', async () => {
  const uris = [
    'viking://resources',
    'viking://agent',
    'viking://session',
    'viking://user/alice',
  ];
  let selected;
  const host = await mount(
    createElement(ReferencePicker, {
      locale: 'en-US',
      config: {
        rows: uris.map((uri) => ({
          kind: 'entry',
          id: uri,
          uri,
          name: uri.split('/').at(-1),
          type: 'directory',
          depth: 0,
        })),
        selected: [],
        onChange() {},
      },
      onSelect: (reference) => (selected = reference.uri),
    }),
  );
  try {
    const picker = host.container.querySelector(
      '.ov-viking-bot__reference-picker',
    );
    assert.ok(picker);
    assert.deepEqual(
      Array.from(picker.querySelectorAll('[data-file-tree-row-id]'), (row) =>
        row.getAttribute('data-file-tree-row-id'),
      ),
      [...uris].reverse(),
    );
    const search = picker.querySelector('input[type="search"]');
    assert.ok(search);
    for (const key of ['ArrowDown', 'Enter']) {
      await act(() =>
        search.dispatchEvent(
          new window.KeyboardEvent('keydown', { key, bubbles: true }),
        ),
      );
    }
    assert.equal(selected, 'viking://session');
  } finally {
    await host.unmount();
  }
});
