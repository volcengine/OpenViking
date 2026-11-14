import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { deferred, mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const resource = 'viking://user/default/resources/readme.txt';
const directory = 'viking://resources';
const memory = 'viking://user/default/memories/profile.txt';
const nextFile = 'viking://resources/next.txt';
const fileTime = '2026-11-11T22:10:00.123Z';
const folderTime = '2026-11-12T11:00:00.000Z';

test('preview footer shows real modification time, text count and a resource-only model notice', async () => {
  const oldStat = deferred();
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/ls') {
      return ok([
        { uri: resource, isDir: false },
        { uri: directory, isDir: true },
        { uri: memory, isDir: false },
        { uri: nextFile, isDir: false },
      ]);
    }
    if (path === '/api/v1/fs/stat') {
      if (uri === resource) {
        return ok({ modTime: '2026-11-11T22:10:00.123456789Z' });
      }
      if (uri === directory) {
        return ok({ isDir: true, updateTime: Date.parse(folderTime) / 1000 });
      }
      if (uri === memory) return oldStat.promise;
      return ok({ mtime: Date.parse(fileTime) / 1000 });
    }
    return ok('');
  });
  const page = (uri, labels) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { uri, locale: 'zh-CN', labels }),
    );
  const view = await mount(page(null));
  const footer = () => view.container.querySelector('.ov-file-preview__footer');
  const time = () => footer()?.querySelector('time');
  const model = () =>
    footer()?.querySelector('.ov-file-browser__preview-model')?.textContent;

  try {
    assert.equal(footer(), null);
    await view.render(page(resource));
    assert.match(footer().textContent, /0 字符数\s*·\s*更新于 /);
    assert.equal(time().dateTime, fileTime);
    assert.equal(model(), '内容解析由 内容理解大模型 提供');

    await view.render(page(directory));
    assert.equal(time().dateTime, folderTime);
    assert.match(footer().textContent, /0 字符数/);
    assert.ok(model());

    await view.render(page(memory));
    assert.equal(time(), null);
    assert.match(footer().textContent, /0 字符数/);
    assert.equal(model(), undefined);

    await view.render(page(nextFile, { previewModelNotice: '' }));
    assert.equal(time().dateTime, fileTime);
    assert.match(footer().textContent, /0 字符数/);
    assert.equal(model(), undefined);
    await act(async () => oldStat.resolve(await ok({ modTime: folderTime })));
    assert.equal(time().dateTime, fileTime);
  } finally {
    await view.unmount();
  }
});
