import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  rootUri,
  target,
} from './helpers/preview-native-dom.mjs';

const cases = [
  [
    `${rootUri}/photo.png`,
    false,
    [],
    ['download', 'exportPack', 'delete'],
    false,
  ],
  [
    `${rootUri}/recording.webm`,
    false,
    [],
    ['download', 'exportPack', 'delete'],
    false,
  ],
  [
    `${rootUri}/guide.md`,
    false,
    [],
    ['download', 'history', 'exportPack', 'delete'],
    true,
  ],
  [`${rootUri}/folder`, true, [], ['importPack', 'exportPack'], false],
  ['viking://system/config.json', false, [], ['download'], false],
  ['viking://system/skills', true, [], [], false],
  ...[
    'viking://user/alice',
    'viking://~',
    'viking://user/alice/peers/agent_x',
    'viking://~/peers/agent_x',
  ].flatMap((home) => [
    [
      `${home}/memories/experiences/a.md`,
      false,
      [],
      ['download', 'history', 'exportPack', 'delete'],
      false,
    ],
    [
      `${home}/memories/experiences/nested/a.md`,
      false,
      [],
      ['download', 'history', 'exportPack', 'delete'],
      true,
    ],
  ]),
  [
    target,
    true,
    [{ to: target }],
    ['history', 'configure', 'importPack', 'exportPack'],
    false,
  ],
  [
    `${target}/child`,
    true,
    [{ to: target }],
    ['history', 'importPack', 'exportPack'],
    false,
  ],
  [
    `${target}/doc.md`,
    false,
    [{ to: target }],
    ['download', 'history', 'exportPack', 'delete'],
    true,
  ],
  [
    `${rootUri}/wiki-other/doc.md`,
    false,
    [{ to: target }],
    ['download', 'history', 'exportPack', 'delete'],
    true,
  ],
  [
    `${target}/.abstract.md`,
    false,
    [{ to: target }],
    ['download', 'exportPack'],
    false,
  ],
];
for (const [uri, isDir, targets, actions, editable] of cases) {
  test(`native resource rules: ${uri}`, async () => {
    const fetch = fixture(uri, isDir, undefined, targets);
    let context;
    const view = await mount(
      tree(fetch, uri, {
        previewSlots: {
          actions(value) {
            context = value;
            return null;
          },
        },
      }),
    );
    try {
      assert.deepEqual(
        context.defaultActions.flat().map((item) => item.key),
        actions,
      );
      assert.equal(context.canEdit, editable);
      if (/\.(png|webm)$/.test(uri)) {
        const footer = view.container.querySelector('.ov-file-preview__footer');
        assert.match(
          footer?.textContent ?? '',
          /Content parsing provided by the Content Understanding Model/,
        );
        assert.doesNotMatch(footer.textContent, /characters?/);
        assert.ok(
          view.container.querySelector('img[data-ov-preview-image],video'),
        );
      }
      if (uri.startsWith('viking://system/'))
        assert.equal(
          fetch.calls.some((call) =>
            /content\/(abstract|overview)$/.test(call.path),
          ),
          false,
        );
    } finally {
      await view.unmount();
    }
  });
}

test('boolean visibility hides defaults without granting resources a capability or revoking custom edit', async () => {
  const uri = `${rootUri}/guide.md`;
  const fetch = fixture(uri, false);
  let context;
  const props = {
    previewSlots: {
      actions(value) {
        context = value;
        return null;
      },
    },
  };
  const view = await mount(tree(fetch, uri, props));
  try {
    const count = fetch.calls.length;
    await view.render(
      tree(fetch, uri, {
        ...props,
        previewActions: {
          edit: false,
          copyPath: false,
          download: false,
          exportPack: false,
          delete: false,
          history: false,
          configure: true,
        },
      }),
    );
    assert.deepEqual(context.defaultActions.flat(), []);
    assert.equal(context.canEdit, true);
    assert.equal(context.canDelete, true);
    assert.equal(
      view.container.querySelector('[aria-label="Copy path"]'),
      null,
    );
    assert.equal(fetch.calls.length, count);
    await view.render(tree(fetch, uri, { ...props, readOnly: true }));
    assert.deepEqual(
      context.defaultActions.flat().map((item) => item.key),
      ['download', 'history', 'exportPack'],
    );
    assert.equal(context.canEdit, false);
  } finally {
    await view.unmount();
  }
});
