import assert from 'node:assert/strict';
import { test } from 'node:test';
import { click, type } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

test('write links the quoted destination and only the URI within quoted content', async () => {
  const destination = 'viking://resources/output folder/out.md';
  const uri = 'viking://resources/a.md';
  const content = `${uri} -> /tmp/a.md`;
  const command = `ov write "${destination}" --content "${content}"`;
  const opened = [];
  const host = await mount(
    async (_url, init) => {
      const body = JSON.parse(init.body);
      assert.equal(body.uri, destination);
      assert.equal(body.content, content);
      return ok({ uri: destination });
    },
    { onOpenUri: (target) => opened.push(target) },
  );
  try {
    async function checkLinks(selector) {
      const region = host.container.querySelector(selector);
      const links = [...region.querySelectorAll('a')];
      assert.equal(region.textContent, command);
      assert.deepEqual(
        links.map((link) => link.getAttribute('href')),
        [destination, uri],
      );
      for (const link of links) await click(link);
    }
    await type(host.input, command);
    await checkLinks('.ov-command-console__input-mirror');
    await host.execute(command);
    await checkLinks('.ov-command-console__bubble-text');
    assert.deepEqual(opened, [destination, uri, destination, uri]);
  } finally {
    await host.dispose();
  }
});

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

test('quoted positional, flag and text arguments keep their spaces in native requests', async () => {
  const source = 'viking://resources/source  folder';
  const target = 'viking://resources/target folder';
  const text = 'release  notes';
  const requests = [];
  const host = await mount(async (url, init) => {
    requests.push(
      init.body
        ? JSON.parse(init.body)
        : Object.fromEntries(new URL(url).searchParams),
    );
    return ok([]);
  });
  try {
    await host.execute(`ov ls "${source}"`);
    await host.execute(`ov cp "${source}" "${target}"`);
    await host.execute(`ov grep --uri="${source}" "${text}"`);
    await host.execute(`ov glob "${text}" -u "${source}"`);
    await host.execute(`ov write "${source}/notes.md" --content "${text}"`);
    await host.execute(`ov mkdir "${target}" --description "${text}"`);
    await host.execute(
      `ov compile create --compile-name alpha --from "${source}" --to "${target}" --skill "${source}/SKILL.md" --instruction "${text}"`,
    );
    assert.equal(requests.length, 7);
    assert.equal(requests[0].uri, source);
    assert.deepEqual(
      [requests[1].from_uri, requests[1].to_uri],
      [source, target],
    );
    assert.deepEqual([requests[2].uri, requests[2].pattern], [source, text]);
    assert.deepEqual([requests[3].uri, requests[3].pattern], [source, text]);
    assert.equal(requests[4].content, text);
    assert.equal(requests[5].description, text);
    assert.deepEqual(
      [
        requests[6].from,
        requests[6].to,
        requests[6].skill,
        requests[6].instruction,
      ],
      [[source], target, source + '/SKILL.md', text],
    );
  } finally {
    await host.dispose();
  }
});
