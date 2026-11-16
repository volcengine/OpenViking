import assert from 'node:assert/strict';
import test from 'node:test';
import { fixture, tree, mount, target } from './helpers/preview-native-dom.mjs';
const cases = [
  ['viking://user/alice/memories/experiences/a.md/', true, false, true, true],
  ['viking://~/memories/experiences/a.md///', true, false, true, true],
  [
    'viking://user/alice/peers/agent_x/memories/experiences/a.md/',
    true,
    false,
    true,
    true,
  ],
  [
    'viking://~/peers/agent_x/memories/experiences/a.md///',
    true,
    false,
    true,
    true,
  ],
  [`${target}/.overview.md/`, false, false, false, true],
  [`${target}/.abstract.md///`, false, false, false, true],
  [`${target}/.relations.json/`, false, true, true, false],
  ['viking://resources/.watch_tasks.json.tmp/', false, false, true, false],
];
for (const [uri, history, edit, canDelete, exportPack] of cases) {
  test(`canonical resource classification: ${uri}`, async () => {
    let context;
    const view = await mount(
      tree(fixture(uri, false, undefined, [{ to: target }]), uri, {
        rootUri: 'viking://',
        previewSlots: {
          actions: (value) => {
            context = value;
            return null;
          },
        },
      }),
    );
    try {
      const commands = context.defaultActions.flat().map((item) => item.key);
      assert.equal(commands.includes('history'), history);
      assert.equal(context.canEdit, edit);
      assert.equal(commands.includes('delete'), canDelete);
      assert.equal(commands.includes('exportPack'), exportPack);
    } finally {
      await view.unmount();
    }
  });
}
