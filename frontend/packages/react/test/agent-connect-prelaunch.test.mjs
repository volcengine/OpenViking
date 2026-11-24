import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement } from 'react';
import { AgentConnect } from '@openviking/react/components/agent-connect';
import { mount } from './helpers/module-dom.mjs';

const pre =
  'https://shms8eb05es6du8q1aoq5.apigateway-cn-beijing.volceapi.com/openviking';
const custom = 'https://custom.example/openviking';
const click = (button) => act(() => button.click());

test('prelaunch endpoint reaches displayed and copied instructions across catalogs and locales', async () => {
  const copied = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (text) => copied.push(text) },
  });
  const props = { apiKey: 'test-key', baseUrl: custom, isPrelaunch: true };
  const host = await mount(createElement(AgentConnect, props));
  try {
    for (const locale of ['en-US', 'zh-CN']) {
      await host.render(createElement(AgentConnect, { ...props, locale }));
      for (const tab of host.container.querySelectorAll(
        '[role="tablist"] > [role="tab"]',
      )) {
        await click(tab);
        for (const item of host.container.querySelectorAll(
          '.ov-agent-connect__item',
        )) {
          await click(item);
          const guides = [
            ...host.container.querySelectorAll(
              '.ov-agent-connect__guides button',
            ),
          ];
          for (const guide of guides.length ? guides : [null]) {
            if (guide) await click(guide);
            const copy = host.container.querySelector(
              '.ov-agent-connect__copy',
            );
            assert.ok(copy, item.textContent);
            await click(copy);
            const text = copied.at(-1);
            assert.ok(text);
            assert.ok(!text.includes(custom), item.textContent);
            // The Doubao connector only asks for a key; it has no endpoint setting.
            if (!/Doubao|豆包/.test(item.textContent)) {
              assert.ok(text.includes(pre), item.textContent);
              assert.ok(
                host.container.textContent.includes(pre),
                item.textContent,
              );
            }
          }
        }
      }
    }
    await host.render(
      createElement(AgentConnect, { ...props, isPrelaunch: false }),
    );
    await click(host.container.querySelector('.ov-agent-connect__copy'));
    assert.ok(copied.at(-1).includes(custom));
    assert.ok(!copied.at(-1).includes(pre));
  } finally {
    await host.unmount();
  }
});
