import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { mount, click, type } from './helpers/tui-dom.mjs';

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
    quickCommands: ['find', 'ls', 'status'],
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
    quickCommands: ['missing', 'ls'],
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
  const unknown = await mount({ commands, quickCommands: ['missing'] });
  try {
    assert.equal(unknown.container.querySelector('.ov-tui__empty'), null);
  } finally {
    await unknown.dispose();
  }
});

test('selecting a quick command fills the input prefix and focuses the composer', async () => {
  const view = await mount({ commands, quickCommands: ['ls', 'status'] });
  try {
    await click(actions(view.container)[0]);
    assert.equal(view.input.value, 'ov ls ');
    assert.equal(document.activeElement, view.input);
    await type(view.input, `${view.input.value}viking://demo`);
    assert.equal(view.input.value, 'ov ls viking://demo');
    // 填入不提交，记录仍为空，空态继续显示。
    assert.equal(actions(view.container).length, 2);
  } finally {
    await view.dispose();
  }
});

test('a host node replaces the built-in empty state and keeps quick commands out', async () => {
  const view = await mount({
    commands,
    quickCommands: ['ls'],
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
    quickCommands: ['ls'],
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
    quickCommands: ['ls'],
    disabled: true,
  });
  try {
    assert.equal(actions(disabled.container)[0].disabled, true);
  } finally {
    await disabled.dispose();
  }
  const readOnly = await mount({ commands, quickCommands: ['ls'] }, false);
  try {
    assert.equal(actions(readOnly.container)[0].disabled, true);
    await click(actions(readOnly.container)[0]);
    assert.equal(readOnly.input.value, '');
  } finally {
    await readOnly.dispose();
  }
});
