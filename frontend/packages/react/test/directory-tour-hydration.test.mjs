import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { settle } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = () => {};
const props = {
  locale: 'en-US',
  open: true,
  steps: [{ id: 'one', target: 'one', title: 'Guide', description: 'Details' }],
};
const surface = () => document.querySelector('.ov-directory-tour');
function target() {
  const anchor = document.createElement('div');
  anchor.getBoundingClientRect = () => ({
    left: 100,
    top: 100,
    width: 120,
    height: 32,
    right: 220,
    bottom: 132,
  });
  document.body.append(anchor);
  return anchor;
}

test('an initially open tour hydrates without recovering or replacing the host tree', async () => {
  const anchor = target();
  const element = h(
    'main',
    null,
    h('input', { defaultValue: 'keep' }),
    h(DirectoryTour, { ...props, locateTarget: async () => anchor }),
  );
  const browserDocument = globalThis.document;
  let html;
  try {
    delete globalThis.document;
    html = renderToString(element);
  } finally {
    globalThis.document = browserDocument;
  }
  const container = document.createElement('div');
  container.innerHTML = html;
  document.body.append(container);
  const input = container.querySelector('input');
  const errors = [];
  let root;
  await act(async () => {
    root = hydrateRoot(container, element, {
      onRecoverableError: (error) => errors.push(error),
    });
  });
  await settle();
  assert.deepEqual(errors, []);
  assert.equal(container.querySelector('input'), input);
  assert.ok(surface());
  await act(async () => root.unmount());
  container.remove();
  anchor.remove();
});
