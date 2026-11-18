import assert from 'node:assert/strict';
import { test } from 'node:test';
import { click } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

test('mkdir links to the complete path returned by the server', async () => {
  const uri = 'viking://resources/中文 目录/a(1).';
  const opened = [];
  const host = await mount(
    async (_url, init) => {
      assert.equal(JSON.parse(init.body).uri, uri);
      return ok({
        uri,
        message: 'See viking://resources/example.md and continue.',
      });
    },
    { onOpenUri: (value) => opened.push(value) },
  );

  try {
    await host.execute(`ov mkdir "${uri}"`);
    const links = [
      ...host.records.at(-1).querySelectorAll('.ov-command-console__write a'),
    ];
    assert.deepEqual(
      links.map((link) => [link.textContent, link.getAttribute('href')]),
      [
        [uri, uri],
        ['viking://resources/example.md', 'viking://resources/example.md'],
      ],
    );
    await click(links[0]);
    assert.deepEqual(opened, [uri]);
  } finally {
    await host.dispose();
  }
});
