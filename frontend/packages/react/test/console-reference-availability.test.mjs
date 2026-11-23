import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement, createRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';
const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);

test('Console only replaces references while the idle bot is available', async () => {
  const initial = {
    uri: 'viking://resources/initial.md',
    name: 'initial.md',
    type: 'file',
  };
  const replacement = {
    uri: 'viking://resources/replacement.md',
    name: 'replacement.md',
    type: 'file',
  };
  const replaceRef = createRef();
  const service = { listSessions: async () => [] };
  let view;
  function Host({ isAvailable }) {
    const [selected, onChange] = useState([initial]);
    view = useConversation(
      {
        conversation: { isAvailable },
        chatOptions: {
          service,
          referenceSelection: { selected, onChange, replaceRef },
        },
      },
      () => {},
    );
    return null;
  }
  const root = createRoot(document.createElement('div'));
  try {
    await act(() => root.render(createElement(Host, { isAvailable: false })));
    assert.equal(view.isSending, false);
    await act(() => replaceRef.current(replacement));
    assert.deepEqual(view.references.selected, [initial]);

    await act(() => root.render(createElement(Host, { isAvailable: true })));
    await act(() => replaceRef.current(replacement));
    assert.deepEqual(view.references.selected, [replacement]);
  } finally {
    await act(() => root.unmount());
  }
});
