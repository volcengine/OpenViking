import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { mount } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
const props = {
  locale: 'en-US',
  open: true,
  steps: [{ id: 'one', target: 'one', title: 'Guide', description: 'Details' }],
};

function adoptedAnchor() {
  const iframe = document.createElement('iframe');
  document.body.append(iframe);
  const anchor = iframe.contentDocument.createElement('button');
  anchor.textContent = 'Adopted anchor';
  const originalConstructor = iframe.contentWindow.HTMLElement;
  document.body.append(document.adoptNode(anchor));
  iframe.remove();
  assert.equal(anchor.ownerDocument, document);
  assert.ok(anchor instanceof originalConstructor);
  assert.equal(anchor instanceof HTMLElement, false);
  anchor.scrollIntoView = () => {};
  anchor.getBoundingClientRect = () => ({
    left: 100,
    top: 100,
    right: 220,
    bottom: 132,
    width: 120,
    height: 32,
  });
  return anchor;
}

for (const group of [false, true]) {
  test(`an adopted iframe element remains a valid ${group ? 'group' : 'single'} anchor`, async () => {
    const anchor = adoptedAnchor();
    const view = await mount(
      h(DirectoryTour, {
        ...props,
        locateTarget: () => Promise.resolve(group ? [anchor] : anchor),
      }),
    );
    try {
      const dialog = document.querySelector('[role="dialog"]');
      assert.ok(dialog);
      assert.ok(dialog.textContent.includes('Details'));
      assert.ok(dialog.querySelector('.ov-directory-tour__highlight'));
      assert.equal(dialog.parentElement.style.visibility, 'visible');
    } finally {
      await view.unmount();
      anchor.remove();
    }
  });
}

test('closing a tour restores focus to an adopted iframe control', async () => {
  const anchor = adoptedAnchor();
  anchor.focus();
  assert.equal(document.activeElement, anchor);
  const view = await mount(
    h(DirectoryTour, {
      ...props,
      locateTarget: () => Promise.resolve([anchor]),
    }),
  );
  assert.equal(document.activeElement?.getAttribute('role'), 'dialog');
  await view.unmount();
  try {
    assert.equal(document.activeElement, anchor);
  } finally {
    anchor.remove();
  }
});
