import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  EmailLoginForm,
  ApiKeyLoginForm,
  PasswordSetupForm,
  PasswordResetRequestForm,
  InvitationConfirmation,
  LoginMethodSelector,
} from '@openviking/react';

const render = (Component, props = {}) =>
  renderToStaticMarkup(createElement(Component, props));
test('auth entries render on the server without a provider or browser globals', () => {
  for (const Component of [
    EmailLoginForm,
    ApiKeyLoginForm,
    PasswordSetupForm,
    PasswordResetRequestForm,
  ]) {
    const html = render(Component);
    assert.match(html, /<form/);
    assert.doesNotMatch(html, /type="checkbox"|<dialog|https:\/\//);
  }
});
test('agreement links are host supplied and never default to a product policy', () => {
  const html = render(EmailLoginForm, {
    agreements: {
      items: [{ label: 'Custom terms', href: 'https://example.com/terms' }],
    },
  });
  assert.match(html, /Custom terms/);
  assert.match(html, /href="https:\/\/example.com\/terms"/);
  assert.match(html, /type="checkbox"/);
  assert.doesNotMatch(html, /<dialog/);
});
test('empty agreement arrays and unconfigured navigation actions stay hidden', () => {
  const html = render(EmailLoginForm, {
    agreements: { items: [] },
    forgotPassword: { label: 'Unwired action' },
  });
  assert.doesNotMatch(html, /type="checkbox"|Unwired action|Forgot password/);
  assert.match(
    render(EmailLoginForm, {
      forgotPassword: { label: 'Recover', onClick() {} },
    }),
    />Recover</,
  );
});
test('multiple forms generate separate labels and error identifiers', () => {
  const html = renderToStaticMarkup(
    createElement(
      'div',
      null,
      createElement(EmailLoginForm),
      createElement(EmailLoginForm),
    ),
  );
  const ids = [...html.matchAll(/ id="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  assert.equal(ids.length, 4);
});
test('password setup preserves account content and configurable policy hint', () => {
  const html = render(PasswordSetupForm, {
    account: 'new@example.com',
    passwordHint: 'Host policy',
    labels: { confirm: 'Create account' },
  });
  assert.match(html, /new@example.com/);
  assert.match(html, /Host policy/);
  assert.match(html, /Create account/);
  assert.equal((html.match(/type="password"/g) || []).length, 2);
});
test('reset delivery state belongs to the submitted email and retry wait is host driven', () => {
  const props = {
    defaultEmail: 'a@example.com',
    sentMessage: { email: 'a@example.com', message: 'Sent to A' },
    retryAfter: 12,
  };
  assert.match(render(PasswordResetRequestForm, props), /Sent to A/);
  assert.match(render(PasswordResetRequestForm, props), /Resend email \(12s\)/);
  assert.doesNotMatch(
    render(PasswordResetRequestForm, {
      ...props,
      defaultEmail: 'b@example.com',
    }),
    /Sent to A/,
  );
});
test('invitation confirmation displays normalized data without accepting on render', () => {
  let accepted = 0;
  const html = render(InvitationConfirmation, {
    details: [{ label: 'Space', value: 'Design' }],
    onAccept: () => accepted++,
    defer: { label: 'Later', onClick() {} },
  });
  assert.match(html, /Design/);
  assert.match(html, /Later/);
  assert.equal(accepted, 0);
});
test('method selection renders only host configured choices', () => {
  const html = render(LoginMethodSelector, {
    methods: [{ id: 'key', label: 'Use a key' }],
  });
  assert.match(html, /Use a key/);
  assert.doesNotMatch(html, /Email/);
});
