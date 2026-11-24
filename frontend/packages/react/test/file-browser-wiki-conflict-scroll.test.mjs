import assert from 'node:assert/strict';
import test from 'node:test';
import { EditorView } from '@codemirror/view';
import { button, click, settle } from './helpers/preview-native-dom.mjs';
import { setEditor, waitForEditor } from './helpers/preview-editor-dom.mjs';
import {
  body,
  fixedSentence,
  fixed,
  mountWikiFile,
  otherFile,
  pending,
} from './helpers/wiki-conflict-editor-dom.mjs';

test('entering Wiki edit mode reveals the first highlighted line without moving the cursor', async (context) => {
  const first = 'The first conflict appears here.';
  const start = `${fixedSentence}\n${'Intro\n'.repeat(100)}`;
  const content = `${start}${first}\n${body}\n<!-- CONFLICTS_CONTENT\n${[
    fixed,
    { ...pending, match_text: 'Missing anchor' },
    pending,
    { ...pending, match_text: first },
  ]
    .map((record) => JSON.stringify(record))
    .join(',\n')}\n-->`;
  const { view, navigate } = await mountWikiFile(
    undefined,
    undefined,
    undefined,
    content,
  );
  const scroll = context.mock.method(EditorView, 'scrollIntoView');
  try {
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    const editor = await waitForEditor(view.container);
    assert.deepEqual(
      scroll.mock.calls.map(({ arguments: args }) => args),
      [[start.length, { y: 'center' }]],
    );
    assert.equal(EditorView.findFromDOM(editor).state.selection.main.head, 0);

    await setEditor(editor, `New first line\n${start}${first}\n${body}`);
    assert.equal(scroll.mock.callCount(), 1);

    await click(button('Cancel', view.container));
    await navigate(otherFile);
    await settle();
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    await waitForEditor(view.container);
    assert.equal(scroll.mock.callCount(), 1);
  } finally {
    await view.unmount();
  }
});

test('each Wiki conflict pencil centers the selected conflict and puts the cursor at its start', async (context) => {
  const first = { ...pending, problem_description: 'First conflict' };
  const later = {
    ...pending,
    match_text: 'manager approval',
    problem_description: 'Later conflict',
  };
  const document = `${body}\n${'Context\n'.repeat(100)}Require manager approval.`;
  const content = `${document}\n<!-- CONFLICTS_CONTENT\n${[first, later]
    .map((record) => JSON.stringify(record))
    .join(',\n')}\n-->`;
  const { view, openDashboard } = await mountWikiFile(
    undefined,
    undefined,
    undefined,
    content,
  );
  const scroll = context.mock.method(EditorView, 'scrollIntoView');
  try {
    for (const conflict of [later, first]) {
      await openDashboard();
      await click(
        view.container.querySelector(
          `button[aria-label="Edit conflict: ${conflict.problem_description}"]`,
        ),
      );
      const editor = await waitForEditor(view.container);
      const position = document.indexOf(conflict.match_text);
      assert.deepEqual(scroll.mock.calls.at(-1)?.arguments, [
        position,
        { y: 'center' },
      ]);
      assert.equal(
        EditorView.findFromDOM(editor).state.selection.main.head,
        position,
      );
      await click(button('Cancel', view.container));
    }
  } finally {
    await view.unmount();
  }
});
