import assert from 'node:assert/strict';
import test from 'node:test';
import { mount, key, click, record } from './helpers/command-console-dom.mjs';

const help = record({
  variant: 'command-help',
  sections: [
    { key: 'core', label: 'Core', items: [{ key: 'read', name: 'read' }] },
  ],
});

test('busy locks quick commands, help, suggestions and submission without an unavailable warning', async () => {
  const submitted = [];
  const view = await mount({
    value: 'ov read',
    busy: true,
    quickCommands: [{ name: 'read', value: 'ov read demo' }],
    onSubmit: (command) => submitted.push(command),
  });
  try {
    assert.equal(view.input.disabled, true);
    assert.equal(
      view.container.querySelector('.ov-command-console__empty-action')
        .disabled,
      true,
    );
    assert.equal(
      view.container.querySelector('.ov-command-console__unavailable'),
      null,
    );
    await view.render({ records: [help] });
    const button = view.container.querySelector(
      '.ov-command-console__help-item',
    );
    assert.equal(button.disabled, true);
    await click(button);
    await key(view.input, 'Tab');
    await key(view.input, 'Enter');
    assert.equal(view.input.value, 'ov read');
    assert.equal(view.container.querySelector('[role="listbox"]'), null);
    assert.deepEqual(submitted, []);
    await view.render({ busy: false });
    assert.equal(view.input.disabled, false);
    assert.equal(button.disabled, false);
    await key(view.input, 'Enter');
    assert.deepEqual(submitted, ['ov read']);
  } finally {
    await view.dispose();
  }
});

test('explicit disabled state keeps unavailable copy, including when busy', async () => {
  for (const busy of [false, true]) {
    const view = await mount({ disabled: true, busy, locale: 'zh-CN' });
    const message = () =>
      view.container.querySelector('.ov-command-console__unavailable')
        ?.textContent;
    try {
      assert.equal(view.input.disabled, true);
      assert.equal(message(), '当前无法执行命令。');
      await view.render({ labels: { unavailableMessage: 'Service stopped' } });
      assert.equal(message(), 'Service stopped');
      await view.render({ labels: { unavailableMessage: '' } });
      assert.equal(message(), '');
      await view.render({
        locale: 'en-US',
        labels: { unavailableMessage: undefined },
      });
      assert.equal(message(), 'Commands are unavailable right now.');
    } finally {
      await view.dispose();
    }
  }
});
