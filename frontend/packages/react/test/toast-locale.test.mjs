import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { useToast, OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';

test('toast inherits nested locale scopes, preserves content on language changes and supports label overrides', async () => {
  let api;
  function Host({ labels }) {
    const [value, holder] = useToast({ labels });
    api = value;
    return holder;
  }
  const host = (locale, labels) =>
    h(
      OpenVikingProvider,
      { locale },
      h(OpenVikingProvider, null, h(Host, { labels })),
    );
  const view = await mount(host('zh-CN'));
  try {
    await act(() => api.warning({ content: 'Caller content', duration: 0 }));
    const card = document.querySelector('.ov-toast');
    assert.equal(
      card.querySelector('button').getAttribute('aria-label'),
      '关闭提示',
    );
    await view.render(host('en-US', { region: 'Feedback', close: undefined }));
    assert.equal(document.querySelector('.ov-toast'), card);
    assert.equal(card.textContent, 'Caller content');
    assert.equal(
      card.querySelector('button').getAttribute('aria-label'),
      'Close message',
    );
    assert.ok(document.querySelector('[role="region"][aria-label="Feedback"]'));
    await view.render(host('en-US', { close: '' }));
    assert.equal(card.querySelector('button').getAttribute('aria-label'), '');
    await act(() => api.clear());
  } finally {
    await view.unmount();
  }
});
