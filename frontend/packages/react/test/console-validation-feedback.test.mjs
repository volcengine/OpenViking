import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount } from './helpers/module-dom.mjs';
const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);
const warning = () =>
  document.querySelector(
    '.ov-toast-stack__item:not(.ov-toast-motion-exit):not(.ov-toast-motion-exit-active) .ov-toast--warning',
  );

test('native attachment validation uses Toast before accepting input and does not replay on locale changes', async () => {
  let state;
  let draftChanges = 0;
  function Host({ locale = 'en-US' }) {
    state = useConversation({ locale }, () => draftChanges++);
    return state.feedback;
  }
  const invalid = {
    text: 'Retain this draft',
    attachments: [
      {
        id: 'image',
        name: 'image.png',
        mimeType: 'image/png',
        previewUrl: 'http://example.com/image.png',
      },
    ],
  };
  const view = await mount(h(Host));
  try {
    await act(() => state.onSend(invalid));
    assert.match(warning().textContent, /HTTPS/);
    assert.equal(draftChanges, 0);
    assert.equal(state.messages.length, 0);
    assert.equal(state.isSending, false);
    assert.equal(view.container.textContent, '');
    assert.equal(
      Boolean(document.querySelector('.ov-notification--error')),
      false,
    );
    await act(() => warning().querySelector('.ov-toast__close').click());
    await view.render(h(Host, { locale: 'zh-CN' }));
    assert.equal(warning(), null);
    await act(() => state.onSend(invalid));
    assert.match(warning().textContent, /图片/);
    assert.equal(draftChanges, 0);
  } finally {
    await view.unmount();
  }
});
