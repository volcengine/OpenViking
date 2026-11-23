import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';
import { act, type } from './helpers/command-console-dom.mjs';

test('the tree palette displays the current flags, defaults, and required URI', async () => {
  const host = await mount(
    async () => assert.fail('Opening the tree hint does not send a request'),
    { locale: 'zh-CN' },
  );
  try {
    await type(host.input, '/tree');
    await act(() => host.container.querySelector('[role="option"]').focus());
    const rows = [...host.container.querySelectorAll('tbody tr')];
    assert.deepEqual(
      rows.map((row) => [row.cells[0].textContent, row.cells[3].textContent]),
      [
        ['uri', '—'],
        ['-L, --level-limit <n>', '3'],
        ['-n, --node-limit <n>', '256'],
        ['-a, --all', 'false'],
        ['-l, --abs-limit <n>', '128'],
      ],
    );
    assert.equal(rows[0].cells[1].textContent, '是');
  } finally {
    await host.dispose();
  }
});

test('tree and ls send Volc filesystem options and defaults to the native endpoints', async () => {
  const calls = [];
  const host = await mount(async (url) => {
    calls.push(new URL(url));
    return ok([]);
  });
  try {
    await host.execute('ov tree viking://resources -L 2 -l=64 -n 20 -a');
    assert.equal(calls.at(-1).pathname, '/gateway/api/v1/fs/tree');
    assert.deepEqual(Object.fromEntries(calls.at(-1).searchParams), {
      uri: 'viking://resources',
      output: 'agent',
      abs_limit: '64',
      show_all_hidden: 'true',
      node_limit: '20',
      level_limit: '2',
    });

    await host.execute(
      'ov tree viking://resources --level_limit 4 --limit 30 --offset 1',
    );
    assert.deepEqual(Object.fromEntries(calls.at(-1).searchParams), {
      uri: 'viking://resources',
      output: 'agent',
      abs_limit: '128',
      show_all_hidden: 'false',
      node_limit: '30',
      level_limit: '4',
      offset: '1',
    });

    await host.execute('ov ls -s -r --all --abs-limit 50');
    assert.equal(calls.at(-1).pathname, '/gateway/api/v1/fs/ls');
    assert.deepEqual(Object.fromEntries(calls.at(-1).searchParams), {
      uri: 'viking://',
      output: 'agent',
      abs_limit: '50',
      show_all_hidden: 'true',
      node_limit: '256',
      simple: 'true',
      recursive: 'true',
    });

    await host.execute('ov tree');
    assert.equal(host.records.at(-1).dataset.status, 'error');
    assert.equal(calls.length, 3);
  } finally {
    await host.dispose();
  }
});

test('write and mkdir send parsed ACL JSON and the write content option', async () => {
  const calls = [];
  const host = await mount(async (url, init) => {
    calls.push({ path: new URL(url).pathname, body: JSON.parse(init.body) });
    return ok({});
  });
  const acl = { acl_mode: 'inherit', entries: [] };
  const aclArgument = `--acl '${JSON.stringify(acl)}'`;
  try {
    await host.execute(`ov mkdir viking://resources/test ${aclArgument}`);
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/fs/mkdir',
      body: { uri: 'viking://resources/test', acl },
    });

    await host.execute(
      `ov write viking://resources/test/example.md --content="hello world" --append ${aclArgument} --wait`,
    );
    assert.deepEqual(calls.at(-1), {
      path: '/gateway/api/v1/content/write',
      body: {
        uri: 'viking://resources/test/example.md',
        content: 'hello world',
        mode: 'append',
        acl,
        wait: true,
      },
    });

    for (const input of [
      'ov write viking://resources/test/example.md',
      'ov write viking://resources/test/example.md --from-file example.md --content duplicate',
      'ov write viking://resources/test/example.md draft --content duplicate',
      'ov write viking://resources/test/example.md --content draft --append --mode create',
    ]) {
      await host.execute(input);
      assert.equal(host.records.at(-1).dataset.status, 'error', input);
    }
    assert.equal(calls.length, 2);
  } finally {
    await host.dispose();
  }
});
