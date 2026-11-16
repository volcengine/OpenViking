import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useLayoutEffect } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileTree } from '@openviking/react/components/file-tree';
import { AgentConnect } from '@openviking/react/components/agent-connect';
import { mount } from './helpers/module-dom.mjs';
import { exposureObserver } from './helpers/exposure-observer.mjs';

const row = {
  kind: 'entry',
  id: 'file',
  name: 'file',
  type: 'file',
  depth: 0,
  uri: 'viking://resources/example.txt',
};
const click = () => document.querySelector('[role="treeitem"]').click();

for (const phase of ['child-layout', 'parent-layout', 'ref']) {
  test(`committed analytics disable/replace applies before ${phase} actions`, async () => {
    const first = [],
      second = [];
    const receiver = (events) => ({
      events: ['openviking_file_tree_entry_select'],
      onEvent: (event) => events.push(event),
    });
    function Child({ revision }) {
      useLayoutEffect(click, [revision]);
      return null;
    }
    function View({ analytics, revision }) {
      useLayoutEffect(() => {
        if (phase === 'parent-layout') click();
      }, [revision]);
      return h(
        OpenVikingProvider,
        { analytics },
        h(FileTree, {
          rows: [row],
          onSelect() {},
          ref:
            phase === 'ref'
              ? (handle) => {
                  if (handle) click();
                }
              : undefined,
          slots:
            phase === 'child-layout'
              ? { toolbar: h(Child, { revision }) }
              : undefined,
        }),
      );
    }
    const mounted = await mount(
      h(View, { analytics: receiver(first), revision: 0 }),
    );
    try {
      assert.equal(first.length, 1);
      await mounted.render(h(View, { analytics: false, revision: 1 }));
      assert.equal(first.length, 1);
      await mounted.render(
        h(View, { analytics: receiver(second), revision: 2 }),
      );
      assert.equal(first.length, 1);
      assert.equal(second.length, 1);
      await mounted.render(
        h(View, { analytics: receiver(first), revision: 3 }),
      );
      assert.equal(first.length, 2);
      assert.equal(second.length, 1);
    } finally {
      await mounted.unmount();
    }
  });
}

test('AgentConnect locale changes preserve exposure; selecting another agent starts a cycle', async () => {
  const observer = exposureObserver();
  const events = [];
  const analytics = { onEvent: (event) => events.push(event.name) };
  const view = (locale, selectedId = 'codex') =>
    h(
      OpenVikingProvider,
      { analytics },
      h(AgentConnect, { apiKey: 'sample-key', locale, selectedId }),
    );
  const mounted = await mount(view('en-US'));
  const count = (name) => events.filter((event) => event === name).length;
  try {
    await act(async () => observer.expose());
    const instructions = 'openviking_agent_connect_instructions_copy_show';
    const block = 'openviking_agent_connect_code_block_copy_show';
    assert.equal(count(instructions), 1);
    const blocks = count(block);
    assert.ok(blocks > 0);
    await mounted.render(view('zh-CN'));
    await act(async () => observer.expose());
    assert.equal(count(instructions), 1);
    assert.equal(count(block), blocks);
    await mounted.render(view('en-US', 'claude-code'));
    await act(async () => observer.expose());
    assert.equal(count(instructions), 2);
    assert.ok(count(block) > blocks);
  } finally {
    await mounted.unmount();
    observer.restore();
  }
});
