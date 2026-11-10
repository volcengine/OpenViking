import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from '@openviking/react/client';
import { useFileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const MEMORY = 'viking://user/default/memories/preferences/default/tooling.md';
const RESOURCE = 'viking://resources/notes/memory-format.md';

const TRAILER =
  '\n\n<!-- MEMORY_FIELDS\n{"version": 3, "memory_type": "preferences"}\n-->\n';

/**
 * 挂载一个只把 Hook 结果交给回调的探针：要断言的是模块产出的页签，不是标记。
 * Mounts a probe handing the Hook's result to a callback: the assertions are on the
 * module's tabs rather than on markup.
 */
function probe(api) {
  return function Probe({ uri }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      controlled: true,
      currentUri: uri,
      onUriChange() {},
    });
    return null;
  };
}

/**
 * 用一份完整的文件内容挂载预览。`content/read` 按 `limit` 切片，与服务端一致——
 * 只回整份内容会让「读满上限」这条分支测不到。
 *
 * Mounts the preview over one full file. `content/read` slices by `limit` as the
 * server does; returning the whole body regardless would never exercise the
 * "the cap was filled" branch.
 */
async function previewOf(uri, body) {
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([{ uri }]);
    if (call.path === '/api/v1/content/read') {
      const limit = Number(call.query?.limit ?? -1);
      const lines = body.split('\n');
      return ok(limit < 0 ? body : lines.slice(0, limit).join('\n'));
    }
    if (call.path === '/api/v1/fs/stat') return ok({ isDir: false });
    return ok('');
  });
  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri }),
    ),
  );
  const tab = api.current.previewTabs?.[0];
  await tree.unmount();
  return { content: tab?.source?.content, truncated: tab?.source?.truncated };
}

test('a memory file loses its trailing MEMORY_FIELDS block', async () => {
  // 宿主把请求变成原始读取时，尾注会原样回来；它属于系统元数据，不是正文。
  //
  // A host that turns the request into a raw read gets the trailer back; it is system
  // metadata rather than body text.
  const { content, truncated } = await previewOf(
    MEMORY,
    `# Tooling\n\nUse pnpm.${TRAILER}`,
  );
  assert.equal(content, '# Tooling\n\nUse pnpm.');
  assert.equal(truncated, false);
});

test('consecutive trailing blocks are all removed', async () => {
  // 记忆文件被重复写入后可能留下多个尾注，只剥最后一个会漏出前一个。
  // Repeated writes can leave several trailers behind; stripping only the last would
  // expose the one before it.
  const { content } = await previewOf(
    MEMORY,
    `# Tooling\n${TRAILER}\n${TRAILER}`,
  );
  assert.equal(content, '# Tooling');
});

test('a same-shaped comment inside the body survives', async () => {
  // 只认位于文件末尾的那一块：正文里举例写出的同形注释属于内容，删掉就是改内容。
  //
  // Only the block that ends the file counts: a same-shaped comment quoted in the body
  // is content, and removing it would rewrite that content.
  const body = `# Format\n\nMemory files end with:\n\n<!-- MEMORY_FIELDS\n{}\n-->\n\nThat is it.${TRAILER}`;
  const { content } = await previewOf(MEMORY, body);
  assert.equal(
    content,
    '# Format\n\nMemory files end with:\n\n<!-- MEMORY_FIELDS\n{}\n-->\n\nThat is it.',
  );
});

test('a resource file keeps the comment verbatim', async () => {
  // 尾注只在记忆命名空间是保留字段；资源与技能文件里的同形注释是正文。
  //
  // The trailer is reserved only in the memory namespace; in resource and skill files a
  // same-shaped comment is body text.
  const body = `# Format\n\nUse this marker:\n\n<!-- MEMORY_FIELDS\n{}\n-->`;
  const { content } = await previewOf(RESOURCE, body);
  assert.equal(content, body);
});

test('a comment that only ends the first page is not a trailer', async () => {
  // 上限内的响应末尾不一定是文件末尾：示例注释正好在 2000 行处结束时，照剥会删掉
  // 真实内容，还会把「被截断」报成「未截断」。
  //
  // The end of a capped response is not necessarily the end of the file: when the
  // example comment happens to end at line 2000, stripping it deletes real content and
  // reports a truncated read as complete.
  const lines = Array.from({ length: 1997 }, (_, i) => `line ${i + 1}`);
  lines.push(
    '<!-- MEMORY_FIELDS',
    '{"example": true}',
    '-->',
    'after the example',
  );
  const { content, truncated } = await previewOf(MEMORY, lines.join('\n'));

  assert.equal(content.split('\n').length, 2000);
  assert.match(content, /<!-- MEMORY_FIELDS/);
  assert.equal(truncated, true);
});

test('a trailer cut off by the cap is still recognised', async () => {
  // 上限内的响应可能把尾注截断在关闭标记之前，那时正则匹配不到、尾注会漏进预览。
  // 只有拿到完整内容才认得出它。
  //
  // A capped response can cut the trailer off before its closing marker; the pattern
  // then does not match and the trailer leaks into the preview. Only the whole file
  // shows it for what it is.
  const lines = Array.from({ length: 1998 }, (_, i) => `line ${i + 1}`);
  lines.push(
    '<!-- MEMORY_FIELDS',
    '{"version": 2}',
    '{"a": 1}',
    '{"b": 2}',
    '-->',
  );
  assert.ok(lines.length > 2000, 'the trailer must straddle the cap');

  const { content, truncated } = await previewOf(MEMORY, lines.join('\n'));
  assert.equal(content.split('\n').length, 1998);
  assert.doesNotMatch(content, /MEMORY_FIELDS/);
  assert.equal(truncated, false);
});
