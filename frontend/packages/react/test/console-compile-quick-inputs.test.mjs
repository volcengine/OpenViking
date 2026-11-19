import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';

const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);
const wiki = '将选中的目录中的文件整理成 Wiki，保留来源和文档之间的关联';
const report = '基于选中的目录生成日报，并设置定时更新';
const reportEn =
  'Generate a daily report from the selected directory and schedule updates';
const reference = {
  name: 'Team',
  uri: 'viking://resources/team',
  type: 'directory',
};

test('Console compile shortcuts fill the draft and leave explicit sending to chat', async () => {
  let view, draft, setDraft, start, finish;
  const requests = [];
  const entered = new Promise((resolve) => {
    start = resolve;
  });
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const service = {
    createSession: async () => ({ id: 'session', title: 'Session' }),
    loadMessages: async () => [],
    listSessions: async () => [],
    removeSession: async () => {},
    async *stream(input) {
      requests.push(input);
      start();
      await pending;
      yield { type: 'complete', text: 'Ready' };
    },
  };
  function Host({ conversation, locale }) {
    [draft, setDraft] = useState('');
    view = useConversation(
      {
        locale,
        chatOptions: { service },
        conversation: {
          references: { rows: [], selected: [reference], onChange() {} },
          ...conversation,
        },
      },
      setDraft,
      draft,
    );
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const render = (conversation, locale = 'zh-CN') =>
    act(() => root.render(createElement(Host, { conversation, locale })));
  const choose = (index) =>
    act(() => view.onQuickInputSelect(view.quickInputs[index]));
  try {
    await render();
    assert.deepEqual(
      view.quickInputs.map(({ label }) => label),
      ['创建 Wiki', '定时日报'],
    );
    await choose(0);
    assert.equal(draft, wiki);
    await choose(0);
    assert.equal(draft, wiki);
    await render(undefined, 'en-US');
    assert.equal(draft, wiki);
    assert.equal(view.quickInputs[1].label, 'Schedule daily report');
    await choose(1);
    assert.equal(draft, reportEn);
    await render();
    assert.equal(draft, reportEn);
    await choose(1);
    assert.equal(draft, report);
    assert.deepEqual(view.references.selected, [reference]);
    await act(() => setDraft('已有自定义草稿'));
    await choose(0);
    assert.equal(draft, '已有自定义草稿');
    assert.equal(requests.length, 0);

    await act(() => setDraft(''));
    await choose(1);
    await act(async () => {
      view.onSend({ text: draft, attachments: [], references: [reference] });
      await entered;
    });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].text, report);
    assert.deepEqual(requests[0].references, [reference]);
    await choose(0);
    assert.equal(draft, '');
    await act(async () => {
      finish();
      await new Promise(setImmediate);
    });

    const changes = [];
    await render({ draft: '', onDraftChange: (value) => changes.push(value) });
    await choose(0);
    assert.deepEqual(changes, [wiki]);
    assert.equal(draft, '');
    await render({ quickInputs: [] });
    assert.deepEqual(view.quickInputs, []);
  } finally {
    finish();
    await act(() => root.unmount());
  }
});
