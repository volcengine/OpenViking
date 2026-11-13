import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import {
  DirectoryTour,
  DirectoryTourIntro,
} from '@openviking/react/components/directory-tour';
import { mount } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;

test('a portalled tour inherits Provider changes without restarting its target lookup', async () => {
  let calls = 0;
  const anchor = document.createElement('div');
  document.body.append(anchor);
  anchor.scrollIntoView = () => {};
  anchor.getBoundingClientRect = () => ({
    left: 10,
    top: 10,
    right: 110,
    bottom: 42,
    width: 100,
    height: 32,
  });
  const locateTarget = async () => {
    calls++;
    return anchor;
  };
  const steps = [
    {
      id: 'host',
      target: 'missing',
      title: 'Host title',
      description: 'Host 原文',
    },
  ];
  const element = (locale, local) =>
    h(
      OpenVikingProvider,
      { locale },
      h(DirectoryTour, {
        open: true,
        locale: local,
        steps,
        locateTarget,
        onClose() {},
      }),
    );
  const view = await mount(element('en-US'));
  try {
    const panel = document.querySelector('.ov-directory-tour');
    const close = () => panel.querySelector('button[aria-label]');
    assert.equal(close().getAttribute('aria-label'), 'Close guide');
    await view.render(element('zh-CN'));
    assert.equal(document.querySelector('.ov-directory-tour'), panel);
    assert.equal(close().getAttribute('aria-label'), '关闭引导');
    assert.match(panel.textContent, /Host title/);
    await view.render(element('zh-CN', 'en-US'));
    assert.equal(close().getAttribute('aria-label'), 'Close guide');
    assert.equal(calls, 1);
  } finally {
    await view.unmount();
    anchor.remove();
  }
});

test('a tour introduction inherits Provider language and preserves explicit overrides', async () => {
  const element = (locale, local) =>
    h(
      OpenVikingProvider,
      { locale },
      h(DirectoryTourIntro, {
        title: 'Host title',
        description: 'Host 原文',
        locale: local,
      }),
    );
  const view = await mount(element('zh-CN'));
  try {
    assert.equal(view.container.querySelector('button').textContent, '知道了');
    await view.render(element('en-US'));
    assert.equal(view.container.querySelector('button').textContent, 'Got it');
    await view.render(element('en-US', 'zh-CN'));
    assert.equal(view.container.querySelector('button').textContent, '知道了');
    assert.match(view.container.textContent, /Host 原文/);
  } finally {
    await view.unmount();
  }
});
