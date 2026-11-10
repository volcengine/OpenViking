import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';
import { act, createElement as h } from 'react';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { mount, settle } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = () => {};
const frames = new Map();
let frameId = 0;
window.requestAnimationFrame = (callback) => {
  frames.set(++frameId, callback);
  return frameId;
};
window.cancelAnimationFrame = (id) => frames.delete(id);
const rect = (left, top, width, height) => ({
  left,
  top,
  width,
  height,
  right: left + width,
  bottom: top + height,
});
const originalRect = HTMLElement.prototype.getBoundingClientRect;
HTMLElement.prototype.getBoundingClientRect = function () {
  return this.classList.contains('ov-directory-tour')
    ? rect(0, 0, 270, 220)
    : originalRect.call(this);
};
const steps = [
  { id: 'one', target: 'one', title: 'Guide', description: 'Details' },
];
const props = { locale: 'en-US', open: true, steps };
const surface = () => document.querySelector('.ov-directory-tour');
async function frame() {
  const pending = [...frames.values()];
  frames.clear();
  await act(async () => pending.forEach((callback) => callback(0)));
}
function target(left = 100, top = 100) {
  const anchor = document.createElement('div');
  anchor.getBoundingClientRect = () => rect(left, top, 120, 32);
  document.body.append(anchor);
  return anchor;
}
afterEach(() => {
  assert.equal(frames.size, 0, 'unmount must cancel layout tracking');
  delete window.visualViewport;
});

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

test('Escape respects a host control and is consumed only when the tour can close', async () => {
  let closed = 0;
  const view = await mount(
    h(DirectoryTour, { ...props, onClose: () => closed++ }),
  );
  const input = document.createElement('input');
  document.body.append(input);
  input.addEventListener('keydown', (event) => event.preventDefault());
  input.dispatchEvent(
    new window.KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    }),
  );
  assert.equal(closed, 0);
  const owned = new window.KeyboardEvent('keydown', {
    key: 'Escape',
    bubbles: true,
    cancelable: true,
  });
  await act(async () => document.dispatchEvent(owned));
  assert.equal(closed, 1);
  assert.equal(owned.defaultPrevented, true);
  await view.render(h(DirectoryTour, props));
  const unowned = new window.KeyboardEvent('keydown', {
    key: 'Escape',
    bubbles: true,
    cancelable: true,
  });
  document.dispatchEvent(unowned);
  assert.equal(unowned.defaultPrevented, false);
  await view.unmount();
  input.remove();
});

test('visual viewport offsets bound both anchored and recovery cards', async () => {
  const viewport = new window.EventTarget();
  Object.assign(viewport, {
    width: 400,
    height: 300,
    offsetLeft: 80,
    offsetTop: 160,
  });
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: viewport,
  });
  const anchor = target(380, 400);
  const view = await mount(
    h(DirectoryTour, { ...props, locateTarget: async () => anchor }),
  );
  assert.equal(surface().parentElement.style.left, '104px');
  assert.equal(surface().parentElement.style.top, '232px');
  assert.equal(
    surface().parentElement.style.getPropertyValue(
      '--ov-ui-directory-tour-viewport-height',
    ),
    '284px',
  );
  assert.equal(surface().parentElement.style.width, '384px');
  viewport.height = 180;
  await act(async () => viewport.dispatchEvent(new window.Event('resize')));
  assert.equal(surface().parentElement.style.top, '168px');
  assert.equal(
    surface().parentElement.style.getPropertyValue(
      '--ov-ui-directory-tour-viewport-height',
    ),
    '164px',
  );
  await view.render(h(DirectoryTour, props));
  await frame();
  assert.equal(surface().parentElement.style.left, '145px');
  await view.unmount();
  anchor.remove();
});

test('a moved but unchanged-size anchor updates the card and highlight next frame', async () => {
  let top = 100;
  const anchor = target();
  anchor.getBoundingClientRect = () => rect(100, top, 120, 32);
  const view = await mount(
    h(DirectoryTour, { ...props, locateTarget: async () => anchor }),
  );
  assert.equal(surface().parentElement.style.top, '84px');
  top += 20;
  await frame();
  assert.equal(surface().parentElement.style.top, '104px');
  assert.equal(
    document.querySelector('.ov-directory-tour__highlight').style.top,
    '120px',
  );
  await view.unmount();
  anchor.remove();
});

test('anchor tokens inherit from a portal parent without overwriting explicit surface tokens', async () => {
  const anchor = target();
  anchor.style.setProperty('--ov-ui-color-primary', 'blue');
  const view = await mount(
    h(DirectoryTour, {
      ...props,
      locateTarget: async () => anchor,
      className: 'host-theme',
      style: { '--ov-ui-color-primary': 'red' },
    }),
  );
  assert.equal(
    surface().style.getPropertyValue('--ov-ui-color-primary'),
    'red',
  );
  assert.equal(
    surface().parentElement.style.getPropertyValue('--ov-ui-color-primary'),
    'blue',
  );
  anchor.style.setProperty('--ov-ui-color-primary', 'green');
  await settle();
  assert.equal(
    surface().style.getPropertyValue('--ov-ui-color-primary'),
    'red',
  );
  assert.equal(
    surface().parentElement.style.getPropertyValue('--ov-ui-color-primary'),
    'green',
  );
  await view.unmount();
  anchor.remove();
});
