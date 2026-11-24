import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  mount,
  key,
  click,
  type,
  record,
  commands,
  act,
} from './helpers/command-console-dom.mjs';

const help = {
  variant: 'command-help',
  sections: [
    { key: 'core', label: 'Core', items: [{ key: 'read', name: 'read' }] },
  ],
};

test('disabled and read-only histories cannot fill help commands or submit', async () => {
  for (const disabled of [false, true]) {
    let changed = 0;
    let submitted = 0;
    const view = await mount(
      {
        records: [record(help)],
        value: 'ov read',
        disabled,
        ...(disabled
          ? {
              onValueChange() {
                changed++;
              },
            }
          : {}),
        onSubmit() {
          submitted++;
        },
      },
      false,
    );
    try {
      const button = view.container.querySelector(
        '.ov-command-console__help-item',
      );
      assert.equal(button.disabled, true);
      await click(button);
      await key(view.input, 'Enter');
      assert.equal(changed, 0);
      assert.equal(submitted, 0);
    } finally {
      await view.dispose();
    }
  }
});

test('Space in the middle of a slash command is ordinary editing', async () => {
  const view = await mount({ value: '/read' });
  try {
    view.input.focus();
    view.input.setSelectionRange(1, 1);
    assert.equal((await key(view.input, ' ')).defaultPrevented, false);
    assert.equal(view.input.value, '/read');
    view.input.setSelectionRange(5, 5);
    assert.equal((await key(view.input, ' ')).defaultPrevented, true);
    assert.equal(view.input.value, 'ov read ');
  } finally {
    await view.dispose();
  }
});

test('keyboard activation of suggestions, examples and help returns focus to the editor', async () => {
  for (const action of ['suggestion', 'example', 'help']) {
    const view = await mount({
      value: action === 'help' ? '' : '/',
      records: action === 'help' ? [record(help)] : [],
    });
    try {
      if (action === 'example') {
        const option = view.container.querySelector('[role="option"]');
        await act(() => option.focus());
      }
      const button = view.container.querySelector(
        action === 'suggestion'
          ? '[role="option"]'
          : action === 'help'
            ? '.ov-command-console__help-item'
            : '.ov-command-console__hint-example',
      );
      assert.ok(button, action);
      await click(button);
      assert.ok(document.activeElement === view.input, action);
      assert.equal(
        view.input.value,
        action === 'example' ? commands[0].examples[0] : 'ov read ',
      );
    } finally {
      await view.dispose();
    }
  }
});

test('IME Escape is ignored by the editor and document listener, including Safari keyCode', async () => {
  const view = await mount({ value: '/' });
  try {
    view.input.focus();
    for (const init of [{ isComposing: true }, { keyCode: 229 }]) {
      const event = await key(view.input, 'Escape', init);
      assert.equal(event.defaultPrevented, false);
      assert.ok(view.container.querySelector('[role="listbox"]'));
    }
    await key(view.input, 'Escape');
    assert.ok(!view.container.querySelector('[role="listbox"]'));
  } finally {
    await view.dispose();
  }
});

test('Tab opens an unselected palette, accepts filtered text, and stays in the input without a completion', async () => {
  const view = await mount({ value: 'ov ' });
  try {
    view.input.focus();
    view.input.setSelectionRange(3, 3);
    await key(view.input, 'Tab');
    assert.ok(view.container.querySelector('[role="listbox"]'));
    assert.equal(view.container.querySelector('[aria-selected="true"]'), null);
    await key(view.input, 'Tab');
    assert.equal(view.input.value, 'ov ');
    await type(view.input, 'ov r');
    await key(view.input, 'Tab');
    assert.equal(view.input.value, 'ov read ');
    assert.equal((await key(view.input, 'Tab')).defaultPrevented, true);
    assert.ok(document.activeElement === view.input);
    assert.equal(
      (await key(view.input, 'Tab', { shiftKey: true })).defaultPrevented,
      false,
    );
  } finally {
    await view.dispose();
  }
});

test('slash multiword discovery stays unselected until navigation or a typed child prefix', async () => {
  const catalogue = ['session', 'session get', 'session list'].map((name) => ({
    name,
    inputText: `ov ${name}`,
    description: name,
    usage: `ov ${name}`,
  }));
  const view = await mount({ value: '/session ', commands: catalogue });
  try {
    await act(() => view.input.focus());
    view.input.setSelectionRange(
      view.input.value.length,
      view.input.value.length,
    );
    assert.ok(view.container.querySelector('[role="listbox"]'));
    assert.ok(!view.container.querySelector('[aria-selected="true"]'));
    await key(view.input, 'Tab');
    assert.equal(view.input.value, '/session ');
    await type(view.input, '/session g');
    await key(view.input, 'Tab');
    assert.equal(view.input.value, 'ov session get ');
    await type(view.input, '/session get');
    await key(view.input, ' ');
    assert.equal(view.input.value, 'ov session get ');
  } finally {
    await view.dispose();
  }
});
