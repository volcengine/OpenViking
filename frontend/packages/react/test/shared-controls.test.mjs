import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, createRef } from 'react';
import { Button, Input, TextArea } from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';

test('Button forwards its native ref and submits only with an explicit htmlType', async () => {
  let submits = 0;
  let clicks = 0;
  const ref = createRef();
  const host = (props) =>
    h(
      'form',
      {
        onSubmit: (event) => {
          event.preventDefault();
          submits++;
        },
      },
      h(
        Button,
        { ref, icon: h('svg'), onClick: () => clicks++, ...props },
        'Save',
      ),
    );
  const view = await mount(host({}));
  try {
    assert.equal(ref.current.tagName, 'BUTTON');
    await act(() => ref.current.click());
    assert.equal(clicks, 1);
    assert.equal(submits, 0);
    await view.render(host({ htmlType: 'submit' }));
    await act(() => ref.current.click());
    assert.equal(submits, 1);
    await view.render(host({ htmlType: 'submit', loading: true }));
    assert.equal(ref.current.getAttribute('aria-busy'), 'true');
    assert.equal(ref.current.disabled, true);
    assert.equal(ref.current.querySelectorAll('svg').length, 0);
    assert.equal(ref.current.textContent, 'Save');
    await act(() => ref.current.click());
    assert.equal(clicks, 2);
    assert.equal(submits, 1);
    await view.render(host({ htmlType: 'submit', disabled: true }));
    await act(() => ref.current.click());
    assert.equal(clicks, 2);
    await view.render(host({ htmlType: 'submit' }));
    await act(() => ref.current.click());
    assert.ok(ref.current.querySelector('svg'));
    assert.equal(
      submits,
      2,
      'loading/disabled can recover without losing the action',
    );
  } finally {
    await view.unmount();
  }
});

test('Input affixes preserve native labeling, combobox semantics, constraints and IME events', async () => {
  const ref = createRef();
  let changed;
  let composing;
  const view = await mount(
    h(
      'form',
      null,
      h('label', { htmlFor: 'lookup' }, 'Principal'),
      h(Input, {
        ref,
        id: 'lookup',
        name: 'principal',
        role: 'combobox',
        'aria-controls': 'candidates',
        'aria-expanded': true,
        'aria-describedby': 'help',
        required: true,
        maxLength: 30,
        prefix: h('svg', null),
        defaultValue: 'alice',
        onChange: (event) => {
          changed = event.currentTarget.value;
        },
        onCompositionEnd: (event) => {
          composing = event.data;
        },
      }),
      h('p', { id: 'help' }, 'Search by name'),
    ),
  );
  try {
    const input = ref.current;
    assert.equal(input.tagName, 'INPUT');
    assert.equal(input.labels[0].textContent, 'Principal');
    assert.equal(input.getAttribute('aria-controls'), 'candidates');
    assert.equal(input.getAttribute('aria-describedby'), 'help');
    assert.equal(input.required, true);
    assert.equal(input.maxLength, 30);
    input.focus();
    assert.equal(document.activeElement, input);
    input.blur();
    await act(() => view.container.querySelector('.ov-input__affix').click());
    assert.equal(
      document.activeElement,
      input,
      'affix clicks focus the native input',
    );
    await act(() => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, '张三');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
      input.dispatchEvent(
        new window.CompositionEvent('compositionend', {
          data: '张三',
          bubbles: true,
        }),
      );
    });
    assert.equal(changed, '张三');
    assert.equal(composing, '张三');
    assert.equal(
      new window.FormData(view.container.querySelector('form')).get(
        'principal',
      ),
      '张三',
    );
  } finally {
    await view.unmount();
  }
});

test('controlled Input and TextArea keep caller-owned values and native readonly/disabled state', async () => {
  const input = createRef(),
    area = createRef();
  const host = (value, disabled) =>
    h(
      'div',
      null,
      h(Input, {
        ref: input,
        value,
        readOnly: true,
        disabled,
        'aria-label': 'Query',
      }),
      h(TextArea, {
        ref: area,
        value,
        readOnly: true,
        disabled,
        'aria-label': 'Answer',
      }),
    );
  const view = await mount(host('First', false));
  try {
    assert.equal(input.current.value, 'First');
    assert.equal(area.current.value, 'First');
    await view.render(host('Second', true));
    assert.equal(input.current.value, 'Second');
    assert.equal(area.current.value, 'Second');
    assert.equal(input.current.readOnly, true);
    assert.equal(area.current.disabled, true);
  } finally {
    await view.unmount();
  }
});
