import { test } from 'node:test';
import { createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { CommandConsole } from '@openviking/react/components/command-console';
import {
  VikingBot,
  VikingBotMessageList,
} from '@openviking/react/components/viking-bot';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import { verifyRetry } from './helpers/error-retry.mjs';

const contentTab = (content) => ({
  key: 'content',
  kind: 'content',
  title: 'Content',
  content,
});
const fileTab = (slots) => ({
  key: 'file',
  kind: 'file',
  loading: true,
  source: { fileName: 'a.txt', content: '' },
  slots,
});
const record = (id, content) => ({
  id,
  command: 'ov health',
  status: 'success',
  content: { variant: 'write-result', lines: [content] },
});

const cases = [
  [
    'FilePreview outer boundary',
    (f, slots) =>
      h(FilePreview, {
        slots,
        tabs: [{ ...contentTab('Content'), title: h(f.Content) }],
      }),
  ],
  [
    'FilePreview content panel',
    (f, slots) => h(FilePreview, { slots, tabs: [contentTab(h(f.Content))] }),
    '[role="tablist"]',
  ],
  [
    'FilePreview body inherited slot',
    (f, slots) =>
      h(FilePreview, {
        slots,
        tabs: [fileTab({ loadingFallback: h(f.Content) })],
      }),
    '[role="tablist"]',
  ],
  [
    'FilePreview body tab override',
    (f, slots) =>
      h(FilePreview, {
        slots: { errorFallback: () => 'Wrong outer fallback' },
        tabs: [fileTab({ ...slots, loadingFallback: h(f.Content) })],
      }),
    '[role="tablist"]',
  ],
  [
    'CommandConsole outer boundary',
    (f, slots) =>
      h(CommandConsole, {
        slots,
        records: [],
        value: '',
        emptyState: h(f.Content),
      }),
  ],
  [
    'CommandConsole individual result',
    (f, slots) =>
      h(CommandConsole, {
        slots,
        records: [
          record('bad', h(f.Content)),
          record('good', 'Healthy result'),
        ],
        value: '',
      }),
    '.ov-command-console__input',
  ],
  [
    'VikingBot outer boundary',
    (f, slots) => h(VikingBot, { slots, messages: [], children: h(f.Content) }),
  ],
  ...['header', 'messageList', 'footer', 'composer', 'interactionSlot'].map(
    (region) => [
      `VikingBot ${region}`,
      (f, slots) =>
        h(VikingBot, {
          slots,
          messages: [],
          composer: h('input', { 'data-healthy-composer': true }),
          ...(region === 'interactionSlot'
            ? {
                interaction: {
                  request: { kind: 'custom', render: () => null },
                },
              }
            : {}),
          [region]: h(f.Content),
        }),
      region === 'composer' || region === 'interactionSlot'
        ? '.ov-viking-bot__empty'
        : '[data-healthy-composer]',
    ],
  ),
  [
    'VikingBotMessageList standalone boundary',
    (f, slots) =>
      h(VikingBotMessageList, {
        slots,
        messages: Object.assign([], {
          slice: () => {
            f.read();
            return [];
          },
        }),
      }),
  ],
  [
    'WikiDashboard',
    (f, slots) =>
      h(WikiDashboard, {
        slots,
        kind: 'directory',
        data: { files: ['viking://resources/a'] },
        defineActions: () => [
          { key: 'action', tooltip: 'Action', icon: h(f.Content) },
        ],
      }),
  ],
  [
    'ExperienceDashboard',
    (f, slots) =>
      h(ExperienceDashboard, {
        slots,
        uri: 'viking://resources',
        kind: 'directory',
        states: {
          list: {
            error: Object.defineProperty(new Error(), 'message', {
              get: f.read,
            }),
          },
        },
      }),
  ],
  [
    'ScopeCascade',
    (f, slots) =>
      h(ScopeCascade, {
        slots,
        loadLibraries: async () => [
          { id: 'library', label: 'Library', icon: h(f.Content) },
        ],
        loadSpaces: async () => [],
        loadUsers: async () => [],
      }),
  ],
];

for (const [name, view, preserve] of cases) {
  test(`${name}: the supplied reset re-renders its failed region and preserves outside state`, (t) =>
    verifyRetry(t, view, preserve));
}
