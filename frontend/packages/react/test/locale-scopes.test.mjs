import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileTree } from '@openviking/react';
import {
  ScopeSelector,
  ScopeSwitcher,
} from '@openviking/react/components/scope-switcher';
import {
  VikingBot,
  VikingBotSessionActions,
} from '@openviking/react/components/viking-bot';
import { Console } from '@openviking/react/modules/console';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';

const tree = (locale) => h(FileTree, { rows: [], locale });
const render = (node) => renderMarkup(node);

test('nested Providers inherit omitted locale and isolate explicit overrides', () => {
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'zh-CN' },
      h(OpenVikingProvider, null, tree()),
      h(OpenVikingProvider, { locale: 'en-US' }, tree()),
      tree(),
    ),
  );
  assert.equal(html.match(/暂无文件/g)?.length, 2);
  assert.equal(html.match(/No files yet/g)?.length, 1);
});

test('module locale scopes host slots and allows component Props to override it', () => {
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'en-US' },
      h(Console, {
        locale: 'zh-CN',
        defaultMode: 'command',
        slots: {
          toolbar: h('section', null, tree(), tree('en-US')),
        },
      }),
    ),
  );
  assert.match(html, /暂无文件/);
  assert.match(html, /No files yet/);
  assert.match(html, /CLI 直接操作上下文/);
});

test('a conversation locale scopes its header and footer while preserving child overrides', () => {
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'zh-CN' },
      h(Console, {
        locale: 'zh-CN',
        conversation: {
          locale: 'en-US',
          headerActions: h('div', null, tree(), tree('zh-CN')),
          footer: tree(),
        },
        slots: { toolbar: tree() },
      }),
    ),
  );
  assert.match(html, /Welcome to VikingBot/);
  assert.doesNotMatch(html, /欢迎使用 VikingBot/);
  assert.equal(html.match(/No files yet/g)?.length, 2);
  assert.equal(html.match(/暂无文件/g)?.length, 2);
});

test('standalone session actions scope their extra slot and isolate sibling content', () => {
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'zh-CN' },
      h(VikingBotSessionActions, {
        locale: 'en-US',
        extra: h('div', null, tree(), tree('zh-CN')),
      }),
      tree(),
    ),
  );
  assert.equal(html.match(/No files yet/g)?.length, 1);
  assert.equal(html.match(/暂无文件/g)?.length, 2);
});

test('VikingBot custom children inherit its local language scope', () => {
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'en-US' },
      h(VikingBot, { messages: [], locale: 'zh-CN' }, tree()),
    ),
  );
  assert.match(html, /暂无文件/);
});

test('ScopeSwitcher propagates its locale to selectors and respects selector overrides', () => {
  const selector = (key, locale) =>
    h(ScopeSelector, {
      key,
      locale,
      kind: 'selectable',
      identity: { key },
      data: { selectedId: '', options: [] },
      state: { visible: true, error: true },
    });
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'en-US' },
      h(
        ScopeSwitcher,
        { locale: 'zh-CN' },
        selector('a'),
        selector('b', 'en-US'),
      ),
    ),
  );
  assert.match(html, /选项加载失败/);
  assert.match(html, /Unable to load options/);
  assert.match(html, /lang="zh-CN"/);
});

test('ScopeCascade derives level names from the Provider when locale is omitted', () => {
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
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'zh-CN' },
      h(ScopeCascade, { controller: { levels: [level, level, level] } }),
    ),
  );
  assert.match(html, /知识库/);
  assert.match(html, /数据空间/);
  assert.match(html, /用户/);
});
