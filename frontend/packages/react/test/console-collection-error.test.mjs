import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { mount, settle } from './helpers/module-dom.mjs';
import { Console } from '@openviking/react/modules/console';

for (const [locale, text] of [
  ['zh-CN', '重试'],
  ['en-US', 'Retry'],
]) {
  test(`collection lookup failures show a usable ${locale} retry action`, async () => {
    let reads = 0;
    const host = await mount(
      h(Console, {
        locale,
        chatOptions: {
          activation: {
            async isEligible() {
              if (++reads === 1) throw new Error('Collection unavailable');
              return false;
            },
            async getStatus() {
              assert.fail('An ineligible collection must not load Bot status');
            },
            async start() {
              assert.fail('Retrying collection lookup must not start Bot');
            },
          },
        },
      }),
    );
    try {
      const retry = [...document.querySelectorAll('button')].find(
        (button) => button.textContent.trim() === text,
      );
      assert.ok(retry);
      await act(async () => retry.click());
      await settle();
      assert.equal(reads, 2);
      assert.equal(
        document.body.textContent.includes('Collection unavailable'),
        false,
      );
    } finally {
      await host.unmount();
    }
  });
}
