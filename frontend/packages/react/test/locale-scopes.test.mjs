import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileTree } from '@openviking/react';
import {
  ScopeSelector,
  ScopeSwitcher,
} from '@openviking/react/components/scope-switcher';
import { VikingBot } from '@openviking/react/components/viking-bot';
import { Console } from '@openviking/react/modules/console';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';

const tree = (locale) => h(FileTree, { rows: [], locale });
const render = (node) => renderToStaticMarkup(node);

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

test('a conversation locale can override its containing Console', () => {
  const html = render(
    h(
      OpenVikingProvider,
      { locale: 'zh-CN' },
      h(Console, { conversation: { locale: 'en-US' } }),
    ),
  );
  assert.match(html, /Welcome to VikingBot/);
  assert.doesNotMatch(html, /欢迎使用 VikingBot/);
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
