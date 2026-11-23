import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, key, act } from './helpers/command-console-dom.mjs';

const findCommand = {
  name: 'find',
  inputText: 'ov find',
  description: 'Find resources',
  usage: 'ov find <query>',
  params: [{ label: '<query>', description: 'Query', required: true }],
};

test('outside press closes the assist before host handlers stop propagation', async () => {
  const view = await mount({ value: '/' });
  const outside = document.createElement('button');
  document.body.append(outside);
  outside.addEventListener('pointerdown', (event) => event.stopPropagation());
  try {
    assert.ok(view.container.querySelector('[role="listbox"]'));
    await act(() =>
      outside.dispatchEvent(
        new window.PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          composed: true,
        }),
      ),
    );
    assert.ok(!view.container.querySelector('[role="listbox"]'));
    assert.equal(view.input.value, '/');
  } finally {
    outside.remove();
    await view.dispose();
  }
});

test('a command can be picked inside a closed shadow root', async () => {
  const view = await mount({ value: '/' }, true, { shadow: 'closed' });
  try {
    const option = view.container.querySelector('[role="option"]');
    assert.ok(option);
    await act(async () => {
      option.dispatchEvent(
        new window.PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          composed: true,
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.ok(option.isConnected, 'the internal press keeps the assist open');
    await act(() => option.click());
    assert.equal(view.input.value, 'ov read ');
    assert.ok(!view.container.querySelector('[role="listbox"]'));
  } finally {
    await view.dispose();
  }
});

test('a press outside a closed shadow host still closes the assist', async () => {
  const view = await mount({ value: '/' }, true, { shadow: 'closed' });
  const outside = document.createElement('button');
  document.body.append(outside);
  try {
    assert.ok(view.container.querySelector('[role="listbox"]'));
    await act(() =>
      outside.dispatchEvent(
        new window.PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          composed: true,
        }),
      ),
    );
    assert.ok(!view.container.querySelector('[role="listbox"]'));
  } finally {
    outside.remove();
    await view.dispose();
  }
});

test('a command can be picked through nested closed shadow roots', async () => {
  const view = await mount({ value: '/' }, true, {
    shadow: 'closed',
    shadowDepth: 2,
  });
  try {
    const option = view.container.querySelector('[role="option"]');
    assert.ok(option);
    await act(async () => {
      option.dispatchEvent(
        new window.PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          composed: true,
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.ok(option.isConnected, 'nested roots keep the internal press open');
    await act(() => option.click());
    assert.equal(view.input.value, 'ov read ');
  } finally {
    await view.dispose();
  }
});

test('a direct press on closed shadow host whitespace closes the assist', async () => {
  const view = await mount({ value: '/' }, true, { shadow: 'closed' });
  try {
    assert.ok(view.container.querySelector('[role="listbox"]'));
    await act(async () => {
      view.hosts[0].dispatchEvent(
        new window.PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          composed: true,
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.ok(!view.container.querySelector('[role="listbox"]'));
  } finally {
    await view.dispose();
  }
});

test('Escape closes the assist after focus leaves the input', async () => {
  const view = await mount({
    value: '/find',
    commands: [findCommand],
  });
  const outside = document.createElement('button');
  document.body.append(outside);
  try {
    await key(view.input, 'ArrowDown');
    await act(() => outside.focus());
    assert.ok(document.activeElement === outside);
    assert.ok(
      view.container.querySelector('.ov-command-console__assist-detail'),
    );
    assert.equal((await key(outside, 'Escape')).defaultPrevented, true);
    assert.ok(
      !view.container.querySelector('.ov-command-console__assist-detail'),
    );
    assert.equal(view.input.value, '/find');
    await act(() => view.input.focus());
    await key(view.input, 'ArrowDown');
    assert.ok(
      view.container.querySelector('.ov-command-console__assist-detail'),
    );
  } finally {
    outside.remove();
    await view.dispose();
  }
});
