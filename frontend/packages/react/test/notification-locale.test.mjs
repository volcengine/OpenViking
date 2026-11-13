import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { renderToString } from 'react-dom/server';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';
import { Host, cards, close } from './helpers/notification-host.mjs';

test('notification follows locale scopes and scoped theme overrides without resetting tasks', async () => {
  let api;
  const host = (locale, labels) =>
    h(
      OpenVikingProvider,
      { locale },
      h(
        'div',
        { style: { '--ov-ui-color-primary': 'rgb(10, 20, 30)' } },
        h(Host, {
          labels,
          capture: (value) => {
            api = value;
          },
        }),
      ),
    );
  const view = await mount(host('zh-CN'));
  try {
    // jsdom omits CSS custom-property inheritance; browser validation covers the ancestor scope.
    await act(() => {
      view.container
        .querySelector('span[hidden]')
        .style.setProperty('--ov-ui-color-primary', 'rgb(10, 20, 30)');
    });
    await act(() => api.info({ content: 'Server text', duration: 0 }));
    const card = cards()[0];
    assert.equal(
      card.querySelector('button').getAttribute('aria-label'),
      '关闭通知',
    );
    assert.equal(
      document
        .querySelector('.ov-notification-stack')
        .style.getPropertyValue('--ov-ui-color-primary'),
      'rgb(10, 20, 30)',
    );
    await view.render(host('en-US', { region: 'My tasks', close: undefined }));
    assert.equal(cards()[0], card);
    assert.equal(card.textContent, 'Server text');
    assert.equal(
      card.querySelector('button').getAttribute('aria-label'),
      'Close notification',
    );
    assert.ok(document.querySelector('[role="region"][aria-label="My tasks"]'));
    await view.render(host('en-US', { close: '' }));
    assert.equal(card.querySelector('button').getAttribute('aria-label'), '');
    const trigger = view.container.querySelector('button');
    trigger.focus();
    assert.equal(document.activeElement, trigger);
    await act(() => api.warning({ content: 'Warning', duration: 0 }));
    assert.equal(
      document.activeElement,
      trigger,
      'new notices do not take focus',
    );
    await act(() => card.querySelector('button').focus());
    await close(card);
    assert.equal(document.activeElement, cards()[0].querySelector('button'));
  } finally {
    await view.unmount();
  }
});

test('notification supports server rendering without document', () => {
  const saved = globalThis.document;
  try {
    delete globalThis.document;
    assert.doesNotThrow(() => renderToString(h(Host, { capture() {} })));
  } finally {
    globalThis.document = saved;
  }
});
