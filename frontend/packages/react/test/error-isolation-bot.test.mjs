import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h, useState } from 'react';
import { VikingBot } from '@openviking/react/components/viking-bot';
import { click, type } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/module-dom.mjs';

function Crash() {
  throw new Error('message rendering failed');
}

test('a failed replacement list preserves composer DOM and draft; input failure preserves the list', async (t) => {
  t.mock.method(console, 'error', () => {});
  function Host({ brokenList, brokenInput }) {
    const [draft, setDraft] = useState('');
    return h(VikingBot, {
      messages: [],
      listKey: 'session-one',
      messageList: brokenList ? h(Crash) : h('p', null, 'Healthy list'),
      composer: brokenInput
        ? h(Crash)
        : h('textarea', {
            value: draft,
            onChange: (event) => setDraft(event.target.value),
          }),
    });
  }
  const host = await mount(h(Host));
  try {
    const input = host.container.querySelector('textarea');
    await type(input, 'conversation draft');
    await host.render(h(Host, { brokenList: true }));
    assert.match(
      host.container.textContent,
      /Message list could not be displayed/,
    );
    assert.equal(host.container.querySelector('textarea'), input);
    assert.equal(input.value, 'conversation draft');
    await host.render(h(Host));
    await click(host.container.querySelector('.ov-error-boundary button'));
    assert.match(host.container.textContent, /Healthy list/);
    await host.render(h(Host, { brokenInput: true }));
    assert.match(
      host.container.textContent,
      /Input area could not be displayed/,
    );
    assert.match(host.container.textContent, /Healthy list/);
    await host.render(h(Host));
    await click(host.container.querySelector('.ov-error-boundary button'));
    assert.equal(
      host.container.querySelector('textarea').value,
      'conversation draft',
    );
  } finally {
    await host.unmount();
  }
});
