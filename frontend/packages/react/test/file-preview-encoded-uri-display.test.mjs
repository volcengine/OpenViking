import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileTree } from '@openviking/react/components/file-tree';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount, settle } from './helpers/module-dom.mjs';

const folderPart = '%E7%9B%AE%E5%BD%95%20%25%2F%E8%B5%84%E6%96%99';
const parent = `viking://resources/${folderPart}`;
const folderLabel = '目录 %/资料';
const cases = [
  ['%E4%B8%AD%E6%96%87%20%25%2F.md', '中文 %/.md'],
  ['raw%name.md', 'raw%name.md'],
  ['%E4%B8.txt', '%E4%B8.txt'],
  ['%25E4%25B8%25AD.md', '%E4%B8%AD.md'],
  ['未编码 中文%.md', '未编码 中文%.md'],
];

for (const [encodedName, expectedName] of cases) {
  test(`FilePreview renders ${encodedName} without changing ancestor navigation`, async (t) => {
    const path = `${parent}/${encodedName}`;
    const opened = [];
    const notified = [];
    const view = await mount(
      h(FilePreview, {
        tabs: [
          {
            kind: 'file',
            key: path,
            source: { path, fileName: encodedName, content: '' },
            operations: {
              openParent: async (uri) => {
                opened.push(uri);
              },
            },
            onOpenParent: (uri) => notified.push(uri),
          },
        ],
      }),
    );
    try {
      await t.test(
        'breadcrumb decodes one level and keeps encoded slash inside a segment',
        () => {
          assert.equal(
            view.container.querySelector('.ov-file-preview__breadcrumb-current')
              ?.textContent,
            expectedName,
          );
          const ancestors = [
            ...view.container.querySelectorAll(
              '.ov-file-preview__breadcrumb-link',
            ),
          ];
          assert.equal(ancestors.length, 2);
          assert.equal(ancestors.at(-1)?.textContent, folderLabel);
        },
      );
      const ancestors = view.container.querySelectorAll(
        '.ov-file-preview__breadcrumb-link',
      );
      await act(() => ancestors[ancestors.length - 1].click());
      await settle();
      assert.deepEqual(opened, [parent]);
      assert.deepEqual(notified, [parent]);
    } finally {
      await view.unmount();
    }
  });
}

test('explicit host titles and source file names keep literal percent text', async () => {
  const view = await mount(
    h(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'host-file',
          title: 'Host %E4%B8%AD label',
          source: { fileName: 'Host %20 file.md', content: '' },
        },
      ],
    }),
  );
  try {
    assert.equal(
      view.container.querySelector('.ov-file-preview__tab-title')?.textContent,
      'Host %E4%B8%AD label',
    );
    assert.equal(
      view.container.querySelector('.ov-file-preview__breadcrumb-current')
        ?.textContent,
      'Host %20 file.md',
    );
  } finally {
    await view.unmount();
  }
});

test('ordinary filesystem paths retain percent sequences in literal names', async () => {
  const view = await mount(
    h(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'local-file',
          source: {
            path: '/tmp/100%20done/report%25.md',
            fileName: 'report%25.md',
            content: '',
          },
        },
      ],
    }),
  );
  try {
    assert.equal(
      view.container.querySelector('.ov-file-preview__breadcrumb-current')
        ?.textContent,
      'report%25.md',
    );
    const ancestors = [
      ...view.container.querySelectorAll('.ov-file-preview__breadcrumb-link'),
    ];
    assert.deepEqual(
      ancestors.map((node) => node.textContent),
      ['/tmp', '100%20done'],
    );
  } finally {
    await view.unmount();
  }
});

test('explicit FileTree row names keep their host-provided text', async () => {
  const view = await mount(
    h(FileTree, {
      isReady: true,
      rows: [
        {
          kind: 'entry',
          id: 'viking://resources/%E4%B8%AD.md',
          uri: 'viking://resources/%E4%B8%AD.md',
          name: 'Host %E4%B8%AD.md',
          depth: 0,
          type: 'file',
        },
      ],
    }),
  );
  try {
    assert.equal(
      view.container.querySelector('.ov-file-tree__label')?.textContent,
      'Host %E4%B8%AD.md',
    );
  } finally {
    await view.unmount();
  }
});

test('an encoded first URI segment displays its name and navigates with the raw URI', async () => {
  const rootUri = 'viking://%E4%B8%AD';
  const opened = [];
  const view = await mount(
    h(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'encoded-root',
          source: {
            path: `${rootUri}/folder/file.md`,
            fileName: 'file.md',
            content: '',
          },
          operations: {
            openParent: async (uri) => {
              opened.push(uri);
            },
          },
        },
      ],
    }),
  );
  try {
    const ancestors = [
      ...view.container.querySelectorAll('.ov-file-preview__breadcrumb-link'),
    ];
    assert.deepEqual(
      ancestors.map((node) => node.textContent),
      ['viking://中', 'folder'],
    );
    await act(() => ancestors[0].click());
    await settle();
    assert.deepEqual(opened, [rootUri]);
  } finally {
    await view.unmount();
  }
});
