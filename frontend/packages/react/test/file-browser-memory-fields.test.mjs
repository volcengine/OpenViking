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

async function previewOf(uri, body) {
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([{ uri }]);
    if (call.path === '/api/v1/content/read') return ok(body);
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
  const content = api.current.previewTabs?.[0]?.source?.content;
  await tree.unmount();
  return content;
}

test('a memory file loses its trailing MEMORY_FIELDS block', async () => {
  // 宿主把请求变成原始读取时，尾注会原样回来；它属于系统元数据，不是正文。
  //
  // A host that turns the request into a raw read gets the trailer back; it is system
  // metadata rather than body text.
  const content = await previewOf(MEMORY, `# Tooling\n\nUse pnpm.${TRAILER}`);
  assert.equal(content, '# Tooling\n\nUse pnpm.');
});

test('consecutive trailing blocks are all removed', async () => {
  // 记忆文件被重复写入后可能留下多个尾注，只剥最后一个会漏出前一个。
  // Repeated writes can leave several trailers behind; stripping only the last would
  // expose the one before it.
  const body = `# Tooling\n${TRAILER}\n${TRAILER}`;
  const content = await previewOf(MEMORY, body);
  assert.equal(content, '# Tooling');
});

test('a same-shaped comment inside the body survives', async () => {
  // 只认位于文件末尾的那一块：正文里举例写出的同形注释属于内容，删掉就是改内容。
  //
  // Only the block that ends the file counts: a same-shaped comment quoted in the body
  // is content, and removing it would rewrite that content.
  const body = `# Format\n\nMemory files end with:\n\n<!-- MEMORY_FIELDS\n{}\n-->\n\nThat is it.${TRAILER}`;
  const content = await previewOf(MEMORY, body);
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
  assert.equal(await previewOf(RESOURCE, body), body);
});
