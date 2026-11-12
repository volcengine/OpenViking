import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  act,
  cloneElement,
  createElement,
  createRef,
  Fragment,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { mount, ok } from './helpers/module-dom.mjs';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';

const emptyFetch = () => ok([]);
const renderModule = (props) =>
  createElement(
    OpenVikingProvider,
    { baseURL: 'https://ov.test', fetch: emptyFetch },
    createElement(FileBrowser, { locale: 'en-US', ...props }),
  );

for (const module of [false, true]) {
  test(`a host popover receives the native button and ref from ${module ? 'FileBrowser' : 'FileBrowserEntries'}`, async () => {
    const anchor = createRef();
    const clicks = { entry: 0, host: 0 };
    function HostPopover({ children }) {
      const [open, setOpen] = useState(false);
      return createElement(
        Fragment,
        null,
        cloneElement(children, {
          ref: anchor,
          onClick(event) {
            children.props.onClick?.(event);
            clicks.host++;
            setOpen(!open);
          },
        }),
        open
          ? createPortal(
              createElement('div', { role: 'dialog' }, 'Host import'),
              document.body,
            )
          : null,
      );
    }
    const props = {
      locale: 'en-US',
      onImportResources: () => clicks.entry++,
      renderImportResources: (trigger) =>
        createElement(HostPopover, null, trigger),
      labels: { importResources: 'Import with host' },
    };
    const host = await mount(
      module
        ? renderModule({ ...props, labels: { entries: props.labels } })
        : createElement(FileBrowserEntries, props),
    );
    try {
      const button = host.container.querySelector('button');
      assert.equal(anchor.current, button);
      assert.equal(button.type, 'button');
      assert.equal(button.textContent, 'Import with host');
      assert.ok(button.classList.contains('ov-file-browser-entries__entry'));
      await act(() => button.click());
      assert.deepEqual(clicks, { entry: 1, host: 1 });
      const panel = document.querySelector('[role="dialog"]');
      assert.equal(panel.textContent, 'Host import');
      assert.ok(!host.container.contains(panel));
    } finally {
      await host.unmount();
    }
    assert.equal(document.querySelector('[role="dialog"]'), null);
  });
}

test('FileBrowser owns entry visibility and passes localized entry copy', async () => {
  let imports = 0;
  const host = await mount(renderModule({}));
  const entries = () =>
    host.container.querySelector('.ov-file-browser-entries');
  try {
    assert.equal(entries(), null);
    await host.render(
      renderModule({
        onImportResources: () => imports++,
        labels: { entries: { importResources: '点击导入' } },
      }),
    );
    const button = entries().querySelector('button');
    assert.equal(button.textContent, '点击导入');
    await act(() => button.click());
    assert.equal(imports, 1);
    await host.render(
      renderModule({
        agentConnect: { apiKey: 'demo-api-key', locale: 'en-US' },
        renderImportResources: () => null,
        labels: { entries: { connectAgent: '连接我的 Agent' } },
      }),
    );
    assert.equal(
      entries().querySelector('button').textContent,
      '连接我的 Agent',
    );
    assert.equal(entries().querySelectorAll('button').length, 1);
    assert.equal(entries().querySelector('.ov-divider'), null);
    await host.render(
      renderModule({
        onImportResources: () => imports++,
        renderImportResources: () => null,
      }),
    );
    assert.equal(entries(), null);
    assert.ok(
      host.container.querySelector(
        '.ov-file-browser__toolbar .ov-file-browser__search-trigger',
      ),
    );
  } finally {
    await host.unmount();
  }
});

test('renderer-only imports are visible, and null removes the entry and divider', async () => {
  const wrapped = (trigger) =>
    createElement('span', { 'data-host-wrapper': '' }, trigger);
  const host = await mount(
    createElement(FileBrowserEntries, {
      locale: 'en-US',
      renderImportResources: wrapped,
    }),
  );
  try {
    assert.ok(host.container.querySelector('[data-host-wrapper] > button'));
    assert.equal(host.container.querySelector('.ov-divider'), null);
    await host.render(
      createElement(FileBrowserEntries, {
        locale: 'en-US',
        onConnectAgent() {},
        onImportResources() {},
        renderImportResources: () => null,
      }),
    );
    assert.equal(host.container.querySelectorAll('button').length, 1);
    assert.equal(host.container.querySelector('.ov-divider'), null);
    assert.equal(
      host.container.querySelector('.ov-file-browser-entries__import-slot'),
      null,
    );
    await host.render(
      createElement(FileBrowserEntries, {
        locale: 'en-US',
        renderImportResources: () => null,
      }),
    );
    assert.equal(host.container.childElementCount, 0);
    await host.render(createElement(FileBrowserEntries, { locale: 'en-US' }));
    assert.equal(host.container.childElementCount, 0);
  } finally {
    await host.unmount();
  }
});

test('a replacement entry owns its interaction', async () => {
  let defaultClicks = 0;
  let hostClicks = 0;
  const host = await mount(
    createElement(FileBrowserEntries, {
      locale: 'en-US',
      onImportResources: () => defaultClicks++,
      renderImportResources: () =>
        createElement(
          'button',
          {
            type: 'button',
            onClick: () => hostClicks++,
          },
          'Custom import',
        ),
    }),
  );
  try {
    await act(() => host.container.querySelector('button').click());
    assert.equal(hostClicks, 1);
    assert.equal(defaultClicks, 0);
  } finally {
    await host.unmount();
  }
});
