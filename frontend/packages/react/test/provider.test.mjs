import assert from 'node:assert/strict';
import test from 'node:test';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { OpenVikingProvider } from '@openviking/react/client';

test('the Provider renders its children', () => {
  const html = renderToStaticMarkup(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test' },
      createElement('span', null, 'inside'),
    ),
  );
  assert.equal(html, '<span>inside</span>');
});

test('mounting the Provider sends no request', () => {
  let called = false;
  const fetch = async () => {
    called = true;
    throw new Error('should not be called');
  };
  renderToStaticMarkup(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement('span', null, 'x'),
    ),
  );
  assert.equal(called, false);
});

test('the Provider accepts both editions of identity fields', () => {
  // 开源版：apiKey 映射为 X-API-Key。商业版：不带 apiKey，靠浏览器 Cookie。
  assert.doesNotThrow(() =>
    renderToStaticMarkup(
      createElement(OpenVikingProvider, {
        baseURL: 'https://ov.test',
        apiKey: 'sk-1',
        accountId: 'acc_1',
        userId: 'user_1',
        resourceId: 'lib_1',
      }),
    ),
  );
  assert.doesNotThrow(() =>
    renderToStaticMarkup(
      createElement(OpenVikingProvider, {
        baseURL: 'https://ov.test',
        accountId: 'acc_1',
        userId: 'user_1',
        resourceId: 'lib_1',
        credentials: 'include',
      }),
    ),
  );
});
