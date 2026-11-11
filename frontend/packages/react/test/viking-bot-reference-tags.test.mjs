import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { act, click, key } from './helpers/tui-dom.mjs';
import './helpers/console-dom.mjs';

const { ReferenceTags } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'reference-tags.js'))
);
const labels = {
  reference: 'References',
  openReference: 'Open {uri}',
  removeReference: 'Remove reference',
};
const source = Object.freeze(
  Array.from({ length: 7 }, (_, i) =>
    Object.freeze({
      name: i === 0 ? 'project' : 'README.md',
      uri:
        i === 0
          ? 'viking://resources/project'
          : `viking://resources/project-${i}/README.md`,
      type: i === 0 ? 'directory' : 'file',
    }),
  ),
);

async function mount() {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let selected;
  let opened;
  function Host() {
    const [items, change] = useState(source);
    selected = items;
    return h(ReferenceTags, {
      config: { selected: items, onChange: change },
      labels,
      onOpenUri: (uri) => {
        opened = uri;
      },
    });
  }
  await act(() => root.render(h(Host)));
  return {
    container,
    get selected() {
      return selected;
    },
    get opened() {
      return opened;
    },
    async dispose() {
      await act(() => root.unmount());
      container.remove();
    },
  };
}

test('references show five full paths and keep identically named files independent', async () => {
  const host = await mount();
  try {
    const paths = [
      ...host.container.querySelectorAll('.ov-viking-bot__reference-path'),
    ];
    assert.deepEqual(
      paths.map((x) => x.textContent),
      source.slice(0, 5).map((x) => x.uri),
    );
    assert.ok(paths.every((x) => x.title === x.textContent));
    assert.equal(
      host.container.querySelector('.ov-viking-bot__reference-more')
        .textContent,
      '+2',
    );
    await click(
      host.container.querySelector(
        `button[aria-label="Open ${source[1].uri}"]`,
      ),
    );
    assert.equal(host.opened, source[1].uri);
    await click(
      host.container.querySelector(
        `button[aria-label="Remove reference: ${source[1].uri}"]`,
      ),
    );
    assert.equal(host.selected.length, 6);
    assert.ok(host.selected.some((x) => x.uri === source[2].uri));
    assert.equal(source.length, 7);
  } finally {
    await host.dispose();
  }
});

test('overflow controls support focus, Escape, and removal down to the visible limit', async () => {
  const host = await mount();
  try {
    let more = host.container.querySelector('.ov-viking-bot__reference-more');
    await act(() => more.focus());
    let dialog = document.querySelector(
      '[role="dialog"][aria-label="References"]',
    );
    assert.equal(
      dialog.hasAttribute('popover'),
      false,
      'the manual fallback owns visibility',
    );
    assert.equal(more.getAttribute('aria-expanded'), 'true');
    assert.equal(
      document.activeElement,
      more,
      'opening on focus must not move focus',
    );
    const open = dialog.querySelector('button');
    await key(more, 'Tab');
    assert.equal(
      document.activeElement?.getAttribute('aria-label'),
      `Open ${source[5].uri}`,
      'Tab enters the overflow controls',
    );
    await key(open, 'Tab', { shiftKey: true });
    assert.equal(
      document.activeElement?.textContent,
      '+2',
      'Shift+Tab returns to the trigger',
    );
    await key(more, 'Tab');
    await key(open, 'Escape');
    assert.equal(more.getAttribute('aria-expanded'), 'false');
    assert.equal(document.activeElement, more);
    await click(more);
    dialog = document.querySelector('[role="dialog"][aria-label="References"]');
    await act(() =>
      dialog.dispatchEvent(
        new window.FocusEvent('focusout', {
          bubbles: true,
          relatedTarget: null,
        }),
      ),
    );
    assert.equal(
      more.getAttribute('aria-expanded'),
      'true',
      'losing a removed control must not close the remaining references',
    );
    await click(
      dialog.querySelector(
        `button[aria-label="Remove reference: ${source[5].uri}"]`,
      ),
    );
    assert.equal(host.selected.length, 6);
    assert.equal(more.textContent, '+1');
    await click(
      dialog.querySelector(
        `button[aria-label="Remove reference: ${source[6].uri}"]`,
      ),
    );
    assert.equal(host.selected.length, 5);
    assert.equal(
      host.container.querySelector('.ov-viking-bot__reference-more'),
      null,
    );
    assert.equal(
      document.querySelector('[role="dialog"][aria-label="References"]'),
      null,
    );
  } finally {
    await host.dispose();
  }
});

test('fallback Tab after the last overflow control resumes the trigger tab order', async () => {
  const host = await mount();
  try {
    const more = host.container.querySelector('.ov-viking-bot__reference-more');
    await act(() => more.focus());
    const dialog = document.querySelector(
      '[role="dialog"][aria-label="References"]',
    );
    const controls = dialog.querySelectorAll('button');
    const last = controls[controls.length - 1];
    await act(() => last.focus());
    const event = await key(last, 'Tab');
    assert.equal(more.getAttribute('aria-expanded'), 'false');
    assert.equal(
      document.activeElement?.textContent,
      '+2',
      'native Tab traversal resumes beside the trigger',
    );
    assert.equal(
      event.defaultPrevented,
      false,
      'Tab can continue outside the non-modal popup',
    );
  } finally {
    await host.dispose();
  }
});
