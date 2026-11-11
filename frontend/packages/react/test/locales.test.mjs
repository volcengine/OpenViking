import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentConnect } from '@openviking/react/components/agent-connect';
import { FileTree } from '@openviking/react/components/file-tree';
import { FilePreview } from '@openviking/react/components/file-preview';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import { ScopeSelector } from '@openviking/react/components/scope-switcher';
import { Tui } from '@openviking/react/components/tui';
import {
  VikingBot,
  VikingBotMessageList,
  VikingBotSessionActions,
} from '@openviking/react/components/viking-bot';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { Console } from '@openviking/react/modules/console';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import { mount } from './helpers/module-dom.mjs';

const render = (Component, props) => renderToStaticMarkup(h(Component, props));
const previewTabs = [
  { kind: 'file', key: 'file', source: { content: '', fileName: 'empty.txt' } },
];
const cases = [
  [AgentConnect, { apiKey: 'demo-key' }, '连接 Agent', 'Connect Agent'],
  [FileTree, { rows: [] }, '暂无文件', 'No files yet'],
  [FilePreview, { tabs: previewTabs }, '该文件为空', 'This file is empty'],
  [
    FileBrowserEntries,
    { onConnectAgent() {}, onImportResources() {} },
    '导入资源',
    'Import resources',
  ],
  [
    ScopeSelector,
    {
      kind: 'selectable',
      identity: { key: 'test' },
      data: { selectedId: '', options: [] },
      state: { visible: true, error: true },
    },
    '选项加载失败',
    'Unable to load options',
  ],
  [
    Tui,
    {
      records: [],
      value: '',
      commands: [{ name: 'ls', description: 'List', usage: 'ov ls' }],
      quickCommands: ['ls'],
    },
    'CLI 直接操作上下文',
    'Work directly with your context through the CLI',
  ],
  [VikingBot, { messages: [] }, '欢迎使用 VikingBot', 'Welcome to VikingBot'],
  [VikingBotMessageList, { messages: [] }, '开始对话', 'Start a conversation'],
  [
    VikingBotSessionActions,
    { sessions: { items: [], onNew() {}, onSelect() {} } },
    '新建会话',
    'New session',
  ],
  [FileBrowser, {}, '选择一个文件查看内容', 'Select a file to preview'],
  [
    Console,
    { defaultMode: 'command' },
    'CLI 直接操作上下文',
    'Work directly with your context through the CLI',
  ],
];

for (const [Component, props, zh, en] of cases) {
  test(`${typeof Component === 'function' ? Component.name : Component.render.name} ships Chinese and English copy`, () => {
    const chinese = render(Component, { ...props, locale: 'zh-CN' });
    const english = render(Component, { ...props, locale: 'en-US' });
    assert.ok(chinese.includes(zh), chinese);
    assert.ok(english.includes(en), english);
    assert.ok(!chinese.includes(en), chinese);
    const defaultEnglish = render(Component, props);
    assert.ok(defaultEnglish.includes(en), defaultEnglish);
    const inherited = renderToStaticMarkup(
      h(OpenVikingProvider, { locale: 'zh-CN' }, h(Component, props)),
    );
    assert.ok(inherited.includes(zh), inherited);
    const overridden = renderToStaticMarkup(
      h(
        OpenVikingProvider,
        { locale: 'zh-CN' },
        h(Component, { ...props, locale: 'en-US' }),
      ),
    );
    assert.ok(overridden.includes(en), overridden);
  });
}

test('changing locale updates copy while preserving host overrides', async () => {
  const props = {
    onConnectAgent() {},
    onImportResources() {},
    labels: { connectAgent: 'Host agent' },
  };
  const view = await mount(
    h(FileBrowserEntries, { ...props, locale: 'zh-CN' }),
  );
  assert.equal(view.container.textContent, 'Host agent导入资源');
  await view.render(h(FileBrowserEntries, { ...props, locale: 'en-US' }));
  assert.equal(view.container.textContent, 'Host agentImport resources');
  await view.unmount();
});

test('tree directory descriptions and per-key overrides use the selected language', () => {
  const props = {
    locale: 'zh-CN',
    rows: [
      {
        kind: 'entry',
        id: 'u',
        uri: 'viking://user/alice',
        name: 'alice',
        type: 'directory',
        depth: 0,
      },
    ],
    labels: { retry: 'Host retry', info: { description: { memories: '' } } },
  };
  assert.match(render(FileTree, props), /当前用户的上下文数据/);
  assert.doesNotMatch(
    render(FileTree, props),
    /Context data for the current user/,
  );
  assert.match(
    render(FileTree, {
      ...props,
      labels: { info: { description: { userRoot: 'Host description' } } },
    }),
    /Host description/,
  );
});

test('preview tab copy overrides outer labels while remaining fields follow locale', () => {
  const html = render(FilePreview, {
    locale: 'zh-CN',
    labels: { fileEmpty: 'Outer empty' },
    tabs: [{ ...previewTabs[0], labels: { fileEmpty: 'Tab empty' } }],
  });
  assert.match(html, /Tab empty/);
  assert.doesNotMatch(html, /Outer empty|This file is empty/);
  const loading = render(FilePreview, {
    locale: 'zh-CN',
    tabs: [{ ...previewTabs[0], loading: true }],
  });
  assert.match(loading, /加载中/);
});

test('undefined preview tab copy inherits outer or locale defaults, while empty strings remain explicit', () => {
  for (const [locale, fallback] of [
    ['zh-CN', '该文件为空'],
    ['en-US', 'This file is empty'],
  ]) {
    const props = {
      locale,
      tabs: [{ ...previewTabs[0], labels: { fileEmpty: undefined } }],
    };
    assert.ok(render(FilePreview, props).includes(fallback));
    assert.match(
      render(FilePreview, { ...props, labels: { fileEmpty: 'Host empty' } }),
      /Host empty/,
    );
    const empty = render(FilePreview, {
      ...props,
      labels: { fileEmpty: 'Host empty' },
      tabs: [{ ...previewTabs[0], labels: { fileEmpty: '' } }],
    });
    assert.doesNotMatch(empty, /Host empty/);
    assert.ok(!empty.includes(fallback));
  }
});

test('ScopeCascade forwards locale to each selector and updates default level names', () => {
  const level = {
    getSnapshot: () => ({
      options: [],
      enabled: true,
      loading: false,
      error: null,
      scopeKey: '',
    }),
    subscribe: () => () => {},
  };
  const html = render(ScopeCascade, {
    locale: 'zh-CN',
    controller: { levels: [level, level, level] },
  });
  assert.match(html, /知识库/);
  assert.match(html, /数据空间/);
  assert.match(html, /用户/);
});
