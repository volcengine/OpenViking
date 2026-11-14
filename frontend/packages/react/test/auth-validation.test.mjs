import assert from 'node:assert/strict';
import test from 'node:test';
import { mount } from './helpers/module-dom.mjs';
import { act, createElement as h } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';

const {
  ApiKeyLoginForm,
  EmailLoginForm,
  PasswordResetRequestForm,
  PasswordSetupForm,
  InvitationConfirmation,
} = await import('@openviking/react');
const { OpenVikingProvider } = await import('@openviking/react/client');

async function submit(view) {
  await act(async () =>
    view.container.querySelector('button[type="submit"]').click(),
  );
}

async function fill(view, index, value) {
  const input = view.container.querySelectorAll('input')[index];
  const { HTMLInputElement, Event } = input.ownerDocument.defaultView;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

for (const [label, required] of [
  ['undefined', undefined],
  ['empty', ''],
]) {
  test(`empty API key is rejected with an ${label} required label`, async () => {
    const received = [];
    const view = await mount(
      h(ApiKeyLoginForm, {
        labels: { required },
        onSubmit: (value) => received.push(value),
      }),
    );
    try {
      await submit(view);
      assert.deepEqual(received, []);
      assert.equal(
        view.container.querySelector('input').getAttribute('aria-invalid'),
        'true',
      );
      if (required === undefined)
        assert.match(view.container.textContent, /This field is required/);
      else assert.equal(view.container.querySelector('[role="alert"]'), null);
      await fill(view, 0, 'demo-key');
      await submit(view);
      assert.deepEqual(received, [{ apiKey: 'demo-key' }]);
    } finally {
      await view.unmount();
    }
  });
}

test('hidden format and mismatch messages still reject invalid form values', async () => {
  const received = [];
  const onSubmit = (value) => received.push(value);
  const view = await mount(
    h(PasswordResetRequestForm, {
      defaultEmail: 'not-an-email',
      labels: { invalidEmail: '' },
      onSubmit,
    }),
  );
  try {
    await submit(view);
    assert.deepEqual(received, []);
    assert.equal(
      view.container.querySelector('[aria-invalid="true"]')?.value,
      'not-an-email',
    );
    await view.render(
      h(PasswordSetupForm, { labels: { mismatch: '' }, onSubmit }),
    );
    await fill(view, 0, 'password-a');
    await fill(view, 1, 'password-b');
    await submit(view);
    assert.deepEqual(received, []);
    assert.equal(
      view.container.querySelector('[aria-invalid="true"]')?.value,
      'password-b',
    );
  } finally {
    await view.unmount();
  }
});

test('auth copy inherits Provider locale and honors local overrides', () => {
  const render = (Component, props = {}) =>
    renderMarkup(
      h(OpenVikingProvider, { locale: 'zh-CN' }, h(Component, props)),
    );
  assert.match(render(EmailLoginForm), /邮箱.*密码.*登录/);
  assert.match(render(ApiKeyLoginForm), /登录/);
  assert.match(render(PasswordResetRequestForm), /发送重置邮件/);
  assert.match(render(PasswordSetupForm), /确认并登录/);
  assert.match(render(InvitationConfirmation, { details: [] }), /接受邀请/);
  assert.match(
    render(InvitationConfirmation, { details: [], acceptLabel: 'Join now' }),
    /Join now/,
  );
  assert.match(
    render(EmailLoginForm, { locale: 'en-US', labels: { login: 'Continue' } }),
    /Email.*Password.*Continue/,
  );
});

test('changing inherited locale updates an existing validation error without resetting the input', async () => {
  const element = (locale) =>
    h(OpenVikingProvider, { locale }, h(PasswordResetRequestForm));
  const view = await mount(element('en-US'));
  try {
    await fill(view, 0, 'invalid-address');
    await submit(view);
    assert.match(view.container.textContent, /Enter a valid email address/);
    await view.render(element('zh-CN'));
    assert.equal(
      view.container.querySelector('input').value,
      'invalid-address',
    );
    assert.match(view.container.textContent, /请输入有效的邮箱地址/);
  } finally {
    await view.unmount();
  }
});
