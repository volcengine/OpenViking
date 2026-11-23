import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';
import { hover } from './helpers/quick-command-widths.mjs';

test('local add-resource opens the host upload page without picking a file or submitting an import', async () => {
  const calls = [];
  let opened = 0;
  const host = await mount(
    async (url, init) => {
      calls.push([new URL(url).pathname, JSON.parse(init.body)]);
      return ok({});
    },
    {
      commandOptions: {
        onOpenLocalResourceImport: () => {
          opened++;
        },
        files: {
          pickFile: async () => assert.fail('Opened the native file picker'),
          download: () => {},
        },
      },
    },
  );
  try {
    await host.execute('ov add-resource "./notes/a file.md"');
    await host.execute('ov add-resource');
    assert.equal(opened, 2);
    assert.match(host.records.at(-1).textContent, /Choose files or folders/);

    await host.execute(
      'ov add-resource ./notes/a.md --parent viking://resources/notes',
    );
    assert.equal(opened, 2);
    assert.match(
      host.records.at(-1).textContent,
      /Use Local upload to configure the import/,
    );
    assert.deepEqual(calls, []);

    await host.execute('ov add-resource https://example.com/notes.md');
    await host.execute(
      'ov add-resource git@github.com:volcengine/OpenViking.git',
    );
    await host.execute('ov add-resource --temp-file-id file-123 --wait');
    assert.deepEqual(calls, [
      ['/gateway/api/v1/resources', { path: 'https://example.com/notes.md' }],
      [
        '/gateway/api/v1/resources',
        { path: 'git@github.com:volcengine/OpenViking.git' },
      ],
      ['/gateway/api/v1/resources', { temp_file_id: 'file-123', wait: true }],
    ]);
    assert.equal(opened, 2);
  } finally {
    await host.dispose();
  }
});

test('the add-resource quick action describes the local upload flow available in the host', async () => {
  const props = {
    locale: 'zh-CN',
    commandOptions: { onOpenLocalResourceImport: () => {} },
  };
  const host = await mount(
    async () => assert.fail('Unexpected request'),
    props,
  );
  try {
    const action = () =>
      [
        ...host.container.querySelectorAll('.ov-command-console__empty-action'),
      ].find((item) => item.textContent.includes('add-resource'));
    assert.match(action().textContent, /导入网页链接、仓库或本地文件/);
    assert.match(
      await hover(action()),
      /仅传入本地路径，会打开「本地上传」页面.*选择文件或文件夹/,
    );
    await host.render({ ...props, locale: 'en-US' });
    assert.match(
      action().textContent,
      /Import a web URL, repository, or local file/,
    );
    assert.match(
      await hover(action()),
      /Run the command by itself or with only a local path to open the Local upload page and choose files or folders/,
    );
    await host.execute('ov help');
    assert.match(
      host.records.at(-1).textContent,
      /open Local upload for files and folders/,
    );
    await host.render(props);
    await host.execute('ov help');
    assert.match(
      host.records.at(-1).textContent,
      /打开「本地上传」选择文件或文件夹/,
    );
  } finally {
    await host.dispose();
  }
});
