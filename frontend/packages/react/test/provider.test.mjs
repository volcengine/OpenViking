import assert from 'node:assert/strict';
import test from 'node:test';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { OpenVikingProvider } from '@openviking/react/client';
import { mergeProviderConfig } from '../src/client/options.ts';

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

test('a nested Provider inherits the address and credential it does not declare', () => {
  const outer = mergeProviderConfig(undefined, {
    baseURL: 'https://ov.test/gateway',
    apiKey: 'outer-key',
    accountId: 'acc_1',
    credentials: 'omit',
  });

  const inner = mergeProviderConfig(outer, { userId: 'other-user' });

  assert.equal(
    inner.baseURL,
    'https://ov.test/gateway',
    'address is inherited',
  );
  assert.equal(inner.identity.apiKey, 'outer-key', 'credential is inherited');
  assert.equal(inner.identity.accountId, 'acc_1');
  assert.equal(inner.identity.userId, 'other-user', 'only userId changes');
  assert.equal(inner.credentials, 'omit', 'cookie policy is inherited');
  assert.equal(
    mergeProviderConfig(outer, { credentials: 'include' }).credentials,
    'include',
    'an explicit inner cookie policy wins',
  );
});

test('an explicit empty value clears the inherited field', () => {
  const outer = mergeProviderConfig(undefined, {
    baseURL: 'https://ov.test',
    apiKey: 'outer-key',
  });

  const inner = mergeProviderConfig(outer, { apiKey: '' });

  assert.equal(inner.identity.apiKey, '', 'the field is explicitly cleared');
});

test('an inner baseURL wins, and an empty one means same origin', () => {
  const outer = mergeProviderConfig(undefined, { baseURL: 'https://ov.test' });

  assert.equal(
    mergeProviderConfig(outer, { baseURL: 'https://other.test' }).baseURL,
    'https://other.test',
  );
  assert.equal(mergeProviderConfig(outer, { baseURL: '' }).baseURL, '');
});

test('an inner Provider inherits the outer transport', () => {
  const outerFetch = async () => new Response('{}');
  const outer = mergeProviderConfig(undefined, {
    baseURL: 'https://ov.test',
    fetch: outerFetch,
  });

  const inner = mergeProviderConfig(outer, { userId: 'other-user' });

  assert.equal(inner.fetch, outerFetch, 'the transport is inherited');
});

test('an inner Provider can replace the transport explicitly', () => {
  const outerFetch = async () => new Response('{}');
  const innerFetch = async () => new Response('{}');
  const outer = mergeProviderConfig(undefined, { fetch: outerFetch });

  assert.equal(
    mergeProviderConfig(outer, { fetch: innerFetch }).fetch,
    innerFetch,
  );
});
