import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { CommandConsole } from '@openviking/react/components/command-console';
import { VikingBot } from '@openviking/react/components/viking-bot';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import { mount, setTextarea } from './helpers/module-dom.mjs';

function Crash() {
  throw new Error('intentional slot test');
}
const custom = (text) =>
  h(
    'aside',
    { className: 'host-error', style: { padding: 23 }, role: 'alert' },
    text,
  );
const record = (id, line) => ({
  id,
  command: 'ov health',
  status: 'success',
  content: { variant: 'write-result', lines: [line] },
});
const tabs = [
  {
    key: 'bad',
    kind: 'file',
    title: 'File',
    source: { fileName: 'demo.txt', content: 'loading' },
    loading: true,
    slots: { loadingFallback: h(Crash) },
  },
];

for (const [name, view] of [
  ['FilePreview', (slots) => h(FilePreview, { tabs, slots })],
  [
    'CommandConsole',
    (slots) =>
      h(CommandConsole, {
        value: '',
        records: [record('bad', h(Crash))],
        slots,
      }),
  ],
  [
    'VikingBot',
    (slots) => h(VikingBot, { messages: [], messageList: h(Crash), slots }),
  ],
  [
    'WikiDashboard',
    (slots) =>
      h(WikiDashboard, {
        kind: 'directory',
        data: { files: ['viking://resources/a'] },
        defineActions: () => [
          { key: 'action', tooltip: 'Action', icon: h(Crash) },
        ],
        slots,
      }),
  ],
  [
    'ExperienceDashboard',
    (slots) =>
      h(ExperienceDashboard, {
        uri: 'viking://resources/a',
        kind: 'directory',
        states: {
          list: {
            error: Object.defineProperty(new Error(), 'message', {
              get() {
                throw new Error('error display crashed');
              },
            }),
          },
        },
        slots,
      }),
  ],
  [
    'ScopeCascade',
    (slots) =>
      h(ScopeCascade, {
        loadLibraries: async () => [
          { id: 'library', label: 'Library', icon: h(Crash) },
        ],
        loadSpaces: async () => [],
        loadUsers: async () => [],
        slots,
      }),
  ],
]) {
  test(`${name} renders the host slot with host styles and can restore its default fallback`, async (t) => {
    t.mock.method(console, 'error', () => {});
    const host = await mount(view({ errorFallback: custom('Custom failure') }));
    try {
      const fallback = host.container.querySelector('.host-error');
      assert.ok(fallback, name);
      assert.equal(fallback.style.padding, '23px');
      assert.equal(host.container.querySelector('.ov-error-boundary'), null);
      assert.equal(
        host.container.querySelector('[slots]'),
        null,
        'slot props are not spread onto DOM',
      );
      await host.render(view({ errorFallback: null }));
      assert.equal(host.container.querySelector('.host-error'), null);
      assert.equal(host.container.querySelector('.ov-error-boundary'), null);
      await host.render(view(undefined));
      assert.ok(host.container.querySelector('.ov-error-boundary'), name);
    } finally {
      await host.unmount();
    }
  });
}

test('FilePreview tab content overrides its component slot, including an explicit null', async (t) => {
  t.mock.method(console, 'error', () => {});
  const tree = (fallback) =>
    h(FilePreview, {
      slots: { errorFallback: custom('Parent failure') },
      tabs: [
        { ...tabs[0], slots: { ...tabs[0].slots, errorFallback: fallback } },
      ],
    });
  const host = await mount(tree(custom('Tab failure')));
  try {
    assert.equal(
      host.container.querySelector('.host-error').textContent,
      'Tab failure',
    );
    await host.render(tree(null));
    assert.equal(host.container.querySelector('.host-error'), null);
    await host.render(tree(undefined));
    assert.equal(
      host.container.querySelector('.host-error').textContent,
      'Parent failure',
    );
  } finally {
    await host.unmount();
  }
});

test('nested component slots override VikingBot inheritance and separate instances do not share slots', async (t) => {
  t.mock.method(console, 'error', () => {});
  const view = (fallback) =>
    h(
      'main',
      null,
      h(VikingBot, {
        messages: [],
        slots: { errorFallback: fallback },
        messageList: h(CommandConsole, {
          records: [record('bad', h(Crash))],
          value: '',
          slots: { errorFallback: custom('Command failure') },
        }),
        composer: h('textarea', {
          'aria-label': 'Conversation draft',
          defaultValue: 'Draft retained',
        }),
      }),
      h(VikingBot, {
        messages: [],
        messageList: h(Crash),
        slots: { errorFallback: custom('Other instance') },
      }),
    );
  const host = await mount(view(custom('Conversation failure')));
  try {
    const input = host.container.querySelector(
      '[aria-label="Conversation draft"]',
    );
    await setTextarea(input, 'Edited draft');
    await host.render(view(custom('Updated parent failure')));
    assert.equal(
      host.container.querySelector('[aria-label="Conversation draft"]'),
      input,
    );
    assert.deepEqual(
      [...host.container.querySelectorAll('.host-error')].map(
        (node) => node.textContent,
      ),
      ['Command failure', 'Other instance'],
    );
    assert.equal(input.value, 'Edited draft');
  } finally {
    await host.unmount();
  }
});
