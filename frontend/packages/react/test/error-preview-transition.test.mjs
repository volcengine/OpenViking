import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';

for (const kind of ['file', 'level']) {
  test(`${kind} switch surfaces a failed background body and its working retry`, async (t) => {
    t.mock.method(console, 'error', () => {});
    let failed = true;
    let attempts = 0;
    function LoadingContent() {
      attempts++;
      if (failed) throw new Error('background body failed');
      return h('p', null, 'Retried loading content');
    }
    const first = { fileName: 'first.md', content: '' };
    const second = {
      fileName: 'second.md',
      url: 'https://example.test/second.md',
    };
    const loadingSlots = { loadingFallback: h(LoadingContent) };
    const tree = (next = false) =>
      h(
        'main',
        null,
        h('input', { defaultValue: 'Preserved host draft' }),
        h(FilePreview, {
          activeKey: kind === 'file' && next ? 'second' : 'first',
          slots: {
            errorFallback: (reset) =>
              h(
                'section',
                { role: 'alert' },
                h('button', { onClick: reset }, 'Retry failed layer'),
              ),
          },
          tabs:
            kind === 'file'
              ? [
                  { key: 'first', kind: 'file', source: first },
                  {
                    key: 'second',
                    kind: 'file',
                    source: second,
                    loading: true,
                    slots: loadingSlots,
                  },
                ]
              : [
                  {
                    key: 'first',
                    kind: 'file',
                    source: first,
                    activeLevelKey: next ? 'second' : 'first',
                    loading: next,
                    slots: loadingSlots,
                    levels: [
                      { key: 'first', label: 'First', source: first },
                      { key: 'second', label: 'Second', source: second },
                    ],
                  },
                ],
        }),
      );
    const host = await mount(tree());
    try {
      const input = host.container.querySelector('input');
      await host.render(tree(true));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 420));
      });
      const retry = host.container.querySelector('[role="alert"] button');
      assert.ok(retry);
      assert.ok(
        !retry.closest('[aria-hidden="true"]'),
        'Retry must be visible',
      );
      assert.ok(
        !host.container.querySelector('.ov-file-preview__layers-loading'),
      );
      const failedAttempts = attempts;
      failed = false;
      await host.render(tree(true));
      assert.equal(attempts, failedAttempts);
      await act(() => retry.click());
      assert.ok(attempts > failedAttempts);
      assert.match(host.container.textContent, /Retried loading content/);
      assert.equal(host.container.querySelector('[role="alert"]'), null);
      assert.equal(host.container.querySelector('input'), input);
      assert.equal(input.value, 'Preserved host draft');
    } finally {
      await host.unmount();
    }
  });
}
