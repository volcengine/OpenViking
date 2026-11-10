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
import { mount } from './helpers/module-dom.mjs';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';

test('a host popover receives the native button, ref and original click handler', async () => {
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
  const host = await mount(
    createElement(FileBrowserEntries, {
      onImportResources: () => clicks.entry++,
      renderImportResources: (trigger) =>
        createElement(HostPopover, null, trigger),
      labels: { importResources: 'Import with host' },
    }),
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

test('renderer-only imports are visible, and null removes the entry and divider', async () => {
  const wrapped = (trigger) =>
    createElement('span', { 'data-host-wrapper': '' }, trigger);
  const host = await mount(
    createElement(FileBrowserEntries, { renderImportResources: wrapped }),
  );
  try {
    assert.ok(host.container.querySelector('[data-host-wrapper] > button'));
    assert.equal(host.container.querySelector('.ov-divider'), null);
    await host.render(
      createElement(FileBrowserEntries, {
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
      createElement(FileBrowserEntries, { renderImportResources: () => null }),
    );
    assert.equal(host.container.childElementCount, 0);
    await host.render(createElement(FileBrowserEntries, {}));
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
