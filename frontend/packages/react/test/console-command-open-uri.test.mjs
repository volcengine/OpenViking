import assert from 'node:assert/strict';
import { test } from 'node:test';
import { click } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const uri = 'viking://resources/notes.md';
const sessionUri = 'viking://session/session_2026111100000_6c4ebf';

// The terminal echoes the command and the result card both render the URI, so
// the test clicks the result link instead of the first anchor in the record.
const resultLink = (host) =>
  host.records.at(-1).querySelector('.ov-command-console__result-body a');

async function mountConsole(props) {
  return mount(async () => ok([{ name: 'notes.md', uri }]), props);
}

test('command mode prefers its own onOpenUri and falls back to the Console callback', async () => {
  const shared = [];
  const commands = [];
  const host = await mountConsole({
    onOpenUri: (value) => shared.push(value),
    command: { onOpenUri: (value) => commands.push(value) },
  });
  try {
    await host.execute(`ov ls ${uri}`);
    await click(resultLink(host));
    assert.deepEqual(commands, [uri]);
    assert.deepEqual(shared, []);
  } finally {
    await host.dispose();
  }
});

test('command mode falls back to the Console callback without a command override', async () => {
  const shared = [];
  const host = await mountConsole({
    onOpenUri: (value) => shared.push(value),
  });
  try {
    await host.execute(`ov ls ${uri}`);
    await click(resultLink(host));
    assert.deepEqual(shared, [uri]);
  } finally {
    await host.dispose();
  }
});

test('search content preserves raw text while title and excerpt links navigate', async () => {
  const content = `See ${uri}, for details.\n[Notes](${uri})\nviking://resources/a;b,(c)[d].md.`;
  const opened = [];
  const host = await mount(
    async () =>
      ok({
        resources: [{ uri, abstract: `See ${uri}, for details.`, content }],
      }),
    { onOpenUri: (value) => opened.push(value) },
  );
  try {
    await host.execute('ov find notes --read-content');
    const record = host.records.at(-1);
    const body = record.querySelector('.ov-command-console__search-content');
    assert.equal(body.textContent, content);
    assert.equal(body.querySelector('a'), null);
    await click(record.querySelector('.ov-command-console__search-open'));
    await host.execute('ov find notes');
    await click(
      host.records
        .at(-1)
        .querySelector('.ov-command-console__search-preview a'),
    );
    assert.deepEqual(opened, [uri, uri]);
  } finally {
    await host.dispose();
  }
});

test('a command override leaves conversation mode on the Console callback', async () => {
  const shared = [];
  const commands = [];
  const conversation = {
    messages: [],
    composer: false,
    sessions: {
      currentId: 'session_2026111100000_6c4ebf',
      currentUri: sessionUri,
      items: [],
      onSelect() {},
      onNew() {},
    },
  };
  const host = await mountConsole({
    defaultMode: 'conversation',
    conversation,
    onOpenUri: (value) => shared.push(value),
    command: { onOpenUri: (value) => commands.push(value) },
  });
  try {
    await host.press('Locate session in the file browser');
    assert.deepEqual(shared, [sessionUri]);
    assert.deepEqual(commands, []);
  } finally {
    await host.dispose();
  }
});
