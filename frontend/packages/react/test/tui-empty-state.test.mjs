import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { mount, click } from './helpers/tui-dom.mjs';

const commands = [
  {
    name: 'ls',
    inputText: 'ov ls',
    description: 'List directory entries',
    usage: 'ov ls [uri]',
  },
  {
    name: 'find',
    inputText: 'ov find',
    summary: 'Find matching context',
    description: 'Unused when a summary exists',
    usage: 'ov find <query>',
  },
  {
    name: 'status',
    description: 'Show system status',
    usage: 'ov status',
  },
];

const hero = (container) => container.querySelector('.ov-tui__empty--hero');
const actions = (container) => [
  ...container.querySelectorAll('.ov-tui__empty-action'),
];

test('quick commands render in host order with name and summary or description', async () => {
  const view = await mount({
    commands,
    quickCommands: [
      { name: 'find', value: '' },
      { name: 'ls', value: '' },
      { name: 'status', value: '' },
    ],
  });
  try {
    assert.ok(hero(view.container));
    assert.deepEqual(
      actions(view.container).map(
        (button) => button.querySelector('.ov-tui__empty-name').textContent,
      ),
      ['find', 'ls', 'status'],
    );
    assert.deepEqual(
      actions(view.container).map(
        (button) => button.querySelector('.ov-tui__empty-desc').textContent,
      ),
      ['Find matching context', 'List directory entries', 'Show system status'],
    );
  } finally {
    await view.dispose();
  }
});

test('unknown quick command names are skipped without dropping the rest', async () => {
  const view = await mount({
    commands,
    quickCommands: [
      { name: 'missing', value: '' },
      { name: 'ls', value: '' },
    ],
  });
  try {
    assert.deepEqual(
      actions(view.container).map(
        (button) => button.querySelector('.ov-tui__empty-name').textContent,
      ),
      ['ls'],
    );
  } finally {
    await view.dispose();
  }
});

test('no empty-state props and no resolved quick command show nothing', async () => {
  const empty = await mount({ commands });
  try {
    assert.equal(empty.container.querySelector('.ov-tui__empty'), null);
  } finally {
    await empty.dispose();
  }
  const unknown = await mount({
    commands,
    quickCommands: [{ name: 'missing', value: '' }],
  });
  try {
    assert.equal(unknown.container.querySelector('.ov-tui__empty'), null);
  } finally {
    await unknown.dispose();
  }
});

test('quick selection preserves the full value, focuses without submitting and honors empty values', async () => {
  const submitted = [];
  const value = '  /ls "viking://resources/a b.md" --limit 3  ';
  const view = await mount({
    commands,
    quickCommands: [
      { name: 'ls', value },
      { name: 'status', value: '' },
    ],
    onSubmit: (command) => submitted.push(command),
  });
  try {
    await click(actions(view.container)[0]);
    assert.equal(view.input.value, value);
    assert.equal(document.activeElement, view.input);
    await click(actions(view.container)[1]);
    assert.equal(view.input.value, '');
    assert.equal(document.activeElement, view.input);
    assert.deepEqual(submitted, []);
    // 填入不提交，记录仍为空，空态继续显示。
    assert.equal(actions(view.container).length, 2);
  } finally {
    await view.dispose();
  }
});

test('a host node replaces the built-in empty state and keeps quick commands out', async () => {
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
    emptyState: createElement('p', null, 'Host empty'),
  });
  try {
    const region = view.container.querySelector('.ov-tui__empty');
    assert.equal(region.textContent, 'Host empty');
    assert.equal(view.container.querySelector('.ov-tui__empty--hero'), null);
    assert.deepEqual(actions(view.container), []);
  } finally {
    await view.dispose();
  }
});

test('a null empty state hides the region even with quick commands', async () => {
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
    emptyState: null,
  });
  try {
    assert.equal(view.container.querySelector('.ov-tui__empty'), null);
    assert.deepEqual(actions(view.container), []);
  } finally {
    await view.dispose();
  }
});

test('disabled and read-only instances render quick commands that cannot be picked', async () => {
  const disabled = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
    disabled: true,
  });
  try {
    assert.equal(actions(disabled.container)[0].disabled, true);
  } finally {
    await disabled.dispose();
  }
  const readOnly = await mount(
    { commands, quickCommands: [{ name: 'ls', value: '' }] },
    false,
  );
  try {
    assert.equal(actions(readOnly.container)[0].disabled, true);
    await click(actions(readOnly.container)[0]);
    assert.equal(readOnly.input.value, '');
  } finally {
    await readOnly.dispose();
  }
});

test('duplicate names render once and a removed command disappears on update', async () => {
  const view = await mount({
    commands,
    quickCommands: [
      { name: 'ls', value: '' },
      { name: 'ls', value: '' },
      { name: 'find', value: '' },
    ],
  });
  try {
    const names = () =>
      actions(view.container).map(
        (button) => button.querySelector('.ov-tui__empty-name').textContent,
      );
    assert.deepEqual(names(), ['ls', 'find']);
    await view.render({
      commands,
      quickCommands: [{ name: 'find', value: '' }],
    });
    assert.deepEqual(names(), ['find']);
  } finally {
    await view.dispose();
  }
});
