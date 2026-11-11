import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement } from 'react';
import { mount, ok } from './helpers/module-dom.mjs';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';

for (const name of ['MutationObserver', 'HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

for (const module of [false, true]) {
  test(`${module ? 'FileBrowser' : 'FileBrowserEntries'} keeps its drawer open when entry locale changes`, async () => {
    let imports = 0;
    const render = (locale, labels) => {
      const props = {
        locale,
        agentConnect: { apiKey: 'demo-key', locale },
        onImportResources: () => imports++,
        renderImportResources: (trigger) =>
          createElement('span', { 'data-import-anchor': '' }, trigger),
        labels: module ? { entries: labels } : labels,
      };
      return module
        ? createElement(
            OpenVikingProvider,
            { fetch: () => ok([]) },
            createElement(FileBrowser, props),
          )
        : createElement(FileBrowserEntries, props);
    };
    const host = await mount(render('zh-CN', { importResources: undefined }));
    const row = () => host.container.querySelector('.ov-file-browser-entries');
    const importButton = () =>
      row().querySelector('[data-import-anchor] > button');
    try {
      assert.equal(row().querySelector('button').textContent, '连接 Agent');
      assert.equal(importButton().textContent, '导入资源');
      await act(() => row().querySelector('button').click());
      const panel = document.querySelector('[aria-modal="true"]');
      assert.ok(panel);
      const close = () => panel.querySelector('.ov-drawer__close');
      assert.equal(close().getAttribute('aria-label'), '关闭');
      assert.equal(panel.closest('.ov-drawer').lang, 'zh-CN');

      await host.render(render('en-US'));
      assert.equal(document.querySelector('[aria-modal="true"]'), panel);
      assert.equal(row().querySelector('button').textContent, 'Connect agent');
      assert.equal(importButton().textContent, 'Import resources');
      assert.equal(close().getAttribute('aria-label'), 'Close');
      assert.equal(panel.closest('.ov-drawer').lang, 'en-US');

      await host.render(
        render('en-US', { close: 'Dismiss connection', importResources: '' }),
      );
      assert.equal(close().getAttribute('aria-label'), 'Dismiss connection');
      assert.equal(importButton().textContent, '');
      assert.equal(row().querySelector('button').textContent, 'Connect agent');
      await act(() => importButton().click());
      assert.equal(imports, 1);
    } finally {
      await host.unmount();
    }
  });
}

test('Provider, module and AgentConnect overrides propagate through the open drawer', async () => {
  const fetch = () => ok([]);
  const element = (provider, module, component) =>
    createElement(
      OpenVikingProvider,
      { locale: provider, fetch },
      createElement(FileBrowser, {
        locale: module,
        agentConnect: { apiKey: 'demo-key', locale: component },
      }),
    );
  const host = await mount(element('zh-CN'));
  try {
    const trigger = host.container.querySelector(
      '.ov-file-browser-entries button',
    );
    assert.equal(trigger.textContent, '连接 Agent');
    await act(() => trigger.click());
    const panel = document.querySelector('[aria-modal="true"]');
    assert.equal(panel.closest('.ov-drawer').lang, 'zh-CN');
    assert.match(panel.textContent, /连接 Agent/);
    await host.render(element('en-US'));
    assert.equal(document.querySelector('[aria-modal="true"]'), panel);
    assert.equal(panel.closest('.ov-drawer').lang, 'en-US');
    assert.match(panel.textContent, /Connect Agent/);
    await host.render(element('en-US', 'zh-CN'));
    assert.equal(trigger.textContent, '连接 Agent');
    assert.equal(panel.closest('.ov-drawer').lang, 'zh-CN');
    await host.render(element('en-US', 'zh-CN', 'en-US'));
    assert.equal(trigger.textContent, '连接 Agent');
    assert.equal(panel.closest('.ov-drawer').lang, 'en-US');
    assert.match(panel.textContent, /Connect Agent/);
  } finally {
    await host.unmount();
  }
});
