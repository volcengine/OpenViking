import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  rootUri,
  click,
} from './helpers/preview-native-dom.mjs';

for (const [name, isDir] of [
  ['guide.md', false],
  ['README', false],
  ['folder', true],
  ['folder.with.dots', true],
]) {
  test(`the native menu exports only directories: ${name}`, async () => {
    const uri = `${rootUri}/${name}`;
    const view = await mount(tree(fixture(uri, isDir), uri));
    try {
      await click(view.container.querySelector('[aria-haspopup="menu"]'));
      const commands = [...document.querySelectorAll('[role="menuitem"]')].map(
        (item) => item.textContent.trim(),
      );
      assert.equal(commands.includes('Export .ovpack'), isDir);
      assert.equal(commands.includes('Download'), !isDir);
    } finally {
      await view.unmount();
    }
  });
}
