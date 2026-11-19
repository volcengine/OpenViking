import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { useConsole } from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';

test('old draft setters cannot overwrite another conversation or a reused adapter', async () => {
  const first = {};
  const second = {};
  let state;
  function Probe({ identity }) {
    state = useConsole({}, identity);
    return null;
  }
  const view = await mount(h(Probe, { identity: first }));
  try {
    await act(() => state.setConversationDraft('Private draft from A'));
    const oldSetter = state.setConversationDraft;
    await view.render(h(Probe, { identity: second }));
    assert.equal(state.conversationDraft, '');
    await act(() => state.setConversationDraft('New draft from B'));
    await act(() =>
      oldSetter(() => assert.fail('The previous owner is stale')),
    );
    assert.equal(state.conversationDraft, 'New draft from B');

    await view.render(h(Probe, { identity: first }));
    assert.equal(state.conversationDraft, '');
    await act(() => oldSetter('A delayed callback'));
    assert.equal(state.conversationDraft, '');
  } finally {
    await view.unmount();
  }
});
