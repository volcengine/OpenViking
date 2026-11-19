import assert from 'node:assert/strict';
import test from 'node:test';
import { act, button, click, ok } from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';
import { setEditor, waitForEditor } from './helpers/preview-editor-dom.mjs';
import {
  mountWikiFile,
  otherFile,
  wikiSaveNotice,
} from './helpers/wiki-conflict-editor-dom.mjs';

const draft = 'The process requires three approvals.';
const finishTransition = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 240)));

async function save(view) {
  await click(view.container.querySelector('button[aria-label="Edit"]'));
  await setEditor(await waitForEditor(view.container), draft);
  await click(button('Save', view.container));
  assert.ok(wikiSaveNotice());
  assert.equal(wikiSaveNotice('success'), null);
}

test('a rejected Wiki write retains the draft and does not show success', async () => {
  const writing = deferred();
  const { view } = await mountWikiFile({}, undefined, writing.promise);
  try {
    await save(view);
    await act(async () =>
      writing.resolve(
        Response.json(
          {
            status: 'error',
            error: { code: 'CONFLICT', message: 'The write was rejected' },
          },
          { status: 409 },
        ),
      ),
    );
    await finishTransition();
    assert.equal(wikiSaveNotice(), null);
    assert.equal(wikiSaveNotice('success'), null);
    assert.match(
      document.querySelector('.ov-notification--error')?.textContent ?? '',
      /The write was rejected/,
    );
    assert.match(
      (await waitForEditor(view.container)).textContent,
      /three approvals/,
    );
  } finally {
    await view.unmount();
  }
});

test('leaving a Wiki file while its write is pending ignores a late successful response', async () => {
  const writing = deferred();
  const { view, fetch, navigate } = await mountWikiFile(
    {},
    undefined,
    writing.promise,
  );
  try {
    await save(view);
    const write = fetch.calls.find(({ path }) =>
      path.endsWith('/content/write'),
    );
    await navigate(otherFile);
    assert.ok(write?.init.signal.aborted);
    await act(async () => writing.resolve(await ok({})));
    await finishTransition();
    assert.equal(wikiSaveNotice(), null);
    assert.equal(wikiSaveNotice('success'), null);
  } finally {
    await view.unmount();
  }
});
