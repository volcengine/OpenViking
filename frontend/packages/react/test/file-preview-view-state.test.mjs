import assert from 'node:assert/strict';
import test from 'node:test';
import { mount } from './helpers/module-dom.mjs';
import { createElement, Suspense, useLayoutEffect } from 'react';
import { useFilePreviewViewState } from '../src/components/file-preview/use-file-preview-view-state.ts';

for (const target of ['another-file', 'another-level']) {
  test(`abandoned transition to ${target} preserves the committed viewer handle`, async () => {
    const handle = { id: 'original-viewer' };
    const pending = new Promise(() => {});
    let committed;
    let attempts = 0;
    function Preview({ identity, suspend }) {
      const state = useFilePreviewViewState(identity);
      const { onHandleChange } = state;
      useLayoutEffect(() => {
        committed = state;
      });
      useLayoutEffect(() => {
        onHandleChange(handle);
        return () => onHandleChange(null);
      }, [onHandleChange]);
      if (suspend) {
        attempts++;
        throw pending;
      }
      return createElement('p', null, identity);
    }
    const render = (identity, suspend = false) =>
      createElement(
        Suspense,
        {
          fallback: 'loading',
        },
        createElement(Preview, { identity, suspend }),
      );
    const view = await mount(render('original'));
    try {
      assert.equal(committed.viewerHandle.current, handle);
      await view.renderTransition(render(target, true));
      assert.ok(attempts > 0, 'the alternate identity must have rendered');
      assert.equal(view.container.textContent, 'original');
      assert.equal(committed.viewerHandle.current, handle);
      await view.render(render('original'));
      assert.equal(committed.viewerHandle.current, handle);
    } finally {
      await view.unmount();
    }
  });
}

test('committed navigation starts without an old handle and ignores late old-viewer cleanup', async () => {
  const states = new Map();
  function Preview({ identity }) {
    const state = useFilePreviewViewState(identity);
    useLayoutEffect(() => {
      states.set(identity, state);
    });
    return null;
  }
  const view = await mount(createElement(Preview, { identity: 'A' }));
  try {
    const a = states.get('A');
    a.onHandleChange({ id: 'A' });
    await view.render(createElement(Preview, { identity: 'B' }));
    const b = states.get('B');
    assert.equal(b.viewerHandle.current, null);
    const handle = { id: 'B' };
    b.onHandleChange(handle);
    a.onHandleChange(null);
    assert.equal(b.viewerHandle.current, handle);
  } finally {
    await view.unmount();
  }
});
