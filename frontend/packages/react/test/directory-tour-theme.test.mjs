import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { mount, settle } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = () => {};
const props = {
  locale: 'en-US',
  open: true,
  steps: [{ id: 'one', target: 'one', title: 'Guide', description: 'Details' }],
};
const surface = () => document.querySelector('.ov-directory-tour');
const token = '--ov-ui-color-primary';
const cssValue = (property, value) => {
  const style = document.createElement('div').style;
  style[property] = value;
  return style[property];
};

function anchorInHost() {
  const host = document.createElement('section');
  const anchor = document.createElement('div');
  anchor.className = 'theme-anchor';
  anchor.getBoundingClientRect = () => ({
    left: 50,
    top: 50,
    width: 120,
    height: 32,
    right: 170,
    bottom: 82,
  });
  host.append(anchor);
  document.body.append(host);
  return { host, anchor };
}

test('ancestor data-theme changes refresh copied tokens without remounting or replacing explicit surface tokens', async () => {
  const { host, anchor } = anchorInHost();
  const css = document.createElement('style');
  css.textContent =
    '[data-theme="blue"] .theme-anchor { --ov-ui-color-primary: blue; } [data-theme="green"] .theme-anchor { --ov-ui-color-primary: green; }';
  document.head.append(css);
  host.dataset.theme = 'blue';
  const background = '--ov-ui-directory-tour-background';
  anchor.style.setProperty(background, 'pink');
  const view = await mount(
    h(DirectoryTour, {
      ...props,
      locateTarget: async () => anchor,
      style: { [token]: 'red' },
    }),
  );
  try {
    const dialog = surface();
    assert.equal(dialog.parentElement.style.getPropertyValue(token), 'blue');
    assert.equal(
      dialog.parentElement.style.getPropertyValue(background),
      'pink',
    );
    await act(async () => {
      host.dataset.theme = 'green';
    });
    await settle();
    assert.equal(surface(), dialog);
    assert.equal(dialog.parentElement.style.getPropertyValue(token), 'green');
    assert.equal(dialog.style.getPropertyValue(token), 'red');
    assert.equal(
      dialog.parentElement.style.getPropertyValue(background),
      'pink',
    );
  } finally {
    await view.unmount();
    host.remove();
    css.remove();
  }
});

test('window and visual viewport resizing refresh media-query-computed tokens with no DOM mutation', async () => {
  const { host, anchor } = anchorInHost();
  const getStyle = window.getComputedStyle;
  let primary = 'blue';
  window.getComputedStyle = (node, ...args) =>
    node === anchor
      ? {
          0: token,
          length: 1,
          getPropertyValue: () => primary,
        }
      : getStyle.call(window, node, ...args);
  const viewport = new window.EventTarget();
  Object.assign(viewport, {
    width: 1000,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
  });
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: viewport,
  });
  const view = await mount(
    h(DirectoryTour, { ...props, locateTarget: async () => anchor }),
  );
  try {
    const dialog = surface();
    assert.equal(dialog.parentElement.style.getPropertyValue(token), 'blue');
    primary = 'red';
    await act(async () => window.dispatchEvent(new window.Event('resize')));
    assert.equal(dialog.parentElement.style.getPropertyValue(token), 'red');
    primary = 'green';
    await act(async () => viewport.dispatchEvent(new window.Event('resize')));
    assert.equal(dialog.parentElement.style.getPropertyValue(token), 'green');
    assert.equal(surface(), dialog);
  } finally {
    await view.unmount();
    window.getComputedStyle = getStyle;
    delete window.visualViewport;
    host.remove();
  }
});

test('stricter numeric and CSS caller dimensions combine with changing viewport limits', async () => {
  const { host, anchor } = anchorInHost();
  const viewport = new window.EventTarget();
  Object.assign(viewport, {
    width: 1000,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
  });
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: viewport,
  });
  const locateTarget = async () => anchor;
  const view = await mount(
    h(DirectoryTour, {
      ...props,
      locateTarget,
      style: { maxWidth: 200, maxHeight: 130 },
    }),
  );
  try {
    assert.equal(
      surface().style.maxWidth,
      cssValue('maxWidth', 'min(200px, 984px)'),
    );
    assert.equal(
      surface().style.maxHeight,
      cssValue('maxHeight', 'min(130px, 784px)'),
    );
    await view.render(
      h(DirectoryTour, {
        ...props,
        locateTarget,
        style: { maxWidth: '12rem', maxHeight: 'calc(100vh - 20px)' },
      }),
    );
    assert.equal(
      surface().style.maxWidth,
      cssValue('maxWidth', 'min(12rem, 984px)'),
    );
    assert.equal(
      surface().style.maxHeight,
      cssValue('maxHeight', 'min(calc(100vh - 20px), 784px)'),
    );
    viewport.width = 180;
    viewport.height = 100;
    await act(async () => viewport.dispatchEvent(new window.Event('resize')));
    assert.equal(
      surface().style.maxWidth,
      cssValue('maxWidth', 'min(12rem, 164px)'),
    );
    assert.equal(
      surface().style.maxHeight,
      cssValue('maxHeight', 'min(calc(100vh - 20px), 84px)'),
    );
  } finally {
    await view.unmount();
    delete window.visualViewport;
    host.remove();
  }
});
