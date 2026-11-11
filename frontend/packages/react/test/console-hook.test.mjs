import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { useConsole } from './helpers/internal-hooks.mjs';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { act } from './helpers/tui-dom.mjs';

test('internal Console state sends quoted and end-of-options data through the native client', async () => {
  const calls = [];
  let state;
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  function Host() {
    state = useConsole();
    return null;
  }
  try {
    await act(() =>
      root.render(
        createElement(
          StrictMode,
          null,
          createElement(
            OpenVikingProvider,
            {
              fetch: async (url, init) => {
                calls.push(JSON.parse(init.body));
                return Response.json({ status: 'ok', result: {} });
              },
            },
            createElement(Host),
          ),
        ),
      ),
    );
    assert.equal(container.childElementCount, 0);
    assert.equal(calls.length, 0);
    for (const input of [
      'ov write viking://resources/a.md "--heading"',
      "ov write viking://resources/a.md '--heading'",
      'ov write viking://resources/a.md -- --heading',
    ]) {
      await act(() => state.execute(input));
      assert.equal(state.records.at(-1).status, 'success');
      assert.equal(calls.at(-1).content, '--heading');
    }
    await act(() =>
      state.execute(
        'ov mkdir viking://resources/demo --description "--example"',
      ),
    );
    assert.equal(calls.at(-1).description, '--example');
    await act(() => state.execute('ov write viking://resources/a.md ""'));
    assert.equal(calls.at(-1).content, '');
    await act(() => state.setCommand('unused draft'));
    await act(() => state.clear());
    assert.equal(state.command, '');
    assert.equal(state.records.length, 0);
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});
