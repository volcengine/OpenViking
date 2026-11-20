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
      if (uri === directory) return ok([{ uri: nextFile, isDir: false }]);
      return ok([
        { uri: resource, isDir: false },
        { uri: directory, isDir: true },
        { uri: memory, isDir: false },
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
    assert.match(footer().textContent, /AI 生成/);

    await view.render(page(memory));
    assert.equal(time(), null);
    assert.match(footer().textContent, /0 字符数/);
    assert.equal(model(), undefined);
    assert.doesNotMatch(footer().textContent, /AI 生成/);

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

test('AI notice follows generated document scope and locale', async () => {
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/ls') return ok([]);
    if (path === '/api/v1/fs/attrs')
      return ok({
        uri,
        attrs: {
          tags: uri?.startsWith('viking://resources/wiki')
            ? ['compile_target=true']
            : [],
        },
      });
    if (path === '/api/v1/fs/stat')
      return ok({
        modTime: fileTime,
        tags: uri?.startsWith('viking://resources/wiki')
          ? ['compile_target=true']
          : [],
      });
    return ok('sample');
  });
  const page = (uri, locale = 'zh-CN') =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { uri, locale }),
    );
  const view = await mount(page(null));
  const meta = () =>
    view.container.querySelector('.ov-file-browser__preview-meta')?.textContent;
  try {
    for (const root of [
      'viking://user/alice',
      'viking://user/alice/peers/editor',
      'viking://~',
      'viking://~/peers/editor',
    ]) {
      for (const kind of [
        'experiences',
        'preferences',
        'entities',
        'cases',
        'trajectory',
        'events',
      ]) {
        await view.render(page(`${root}/memories/${kind}/nested/example.md`));
        assert.match(meta(), /AI 生成/);
      }
      for (const name of ['identity.md', 'soul.md']) {
        await view.render(page(`${root}/${name}`));
        assert.match(meta(), /AI 生成/);
      }
    }
    await view.render(page('viking://resources/wiki/chapter.md'));
    assert.match(meta(), /AI 生成/);
    await view.render(page('viking://system/compile/task/output.md', 'en-US'));
    assert.match(meta(), /AI generated/);
    for (const uri of [
      'viking://resources/raw.md',
      'viking://user/alice/resources/events/raw.md',
      'viking://user/alice/sessions/run/messages.jsonl',
    ]) {
      await view.render(page(uri));
      assert.doesNotMatch(meta(), /AI 生成/);
    }
    await view.render(page('viking://resources/raw/.abstract.md'));
    assert.match(meta(), /AI 生成/);
    await view.render(page('viking://resources/raw/.overview.md'));
    assert.match(meta(), /AI 生成/);
  } finally {
    await view.unmount();
  }
});
