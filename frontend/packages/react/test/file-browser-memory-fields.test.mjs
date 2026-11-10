import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
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
 * 按服务端的方式切行：Python 的 `splitlines(keepends=True)`，每行保留自己的换行
 * 符，`\r\n` 算一个分隔符。用 `split('\n')` 做桩会掩盖行边界不一致的缺陷。
 *
 * Splits lines the way the server does — Python's `splitlines(keepends=True)`, each
 * line keeping its own terminator with `\r\n` counting as one. Stubbing it with
 * `split('\n')` would hide a disagreement over line boundaries.
 */
function serverSplitLines(text) {
  // 独立于被测实现再写一遍，避免两边共用同一个错误。
  // Written independently of the implementation under test so the two cannot share a
  // mistake.
  const breaks = new Set([
    0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029,
  ]);
  if (!text) return [];
  const lines = [];
  let begin = 0;
  for (let i = 0; i < text.length; i++) {
    if (!breaks.has(text.charCodeAt(i))) continue;
    const paired =
      text.charCodeAt(i) === 0x0d && text.charCodeAt(i + 1) === 0x0a;
    const stop = i + (paired ? 2 : 1);
    lines.push(text.slice(begin, stop));
    begin = stop;
    i = stop - 1;
  }
  if (begin < text.length) lines.push(text.slice(begin));
  return lines;
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
      const lines = serverSplitLines(body);
      return ok(limit < 0 ? body : lines.slice(0, limit).join(''));
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

  assert.equal(serverSplitLines(content).length, 2000);
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
  assert.equal(serverSplitLines(content).length, 1998);
  assert.doesNotMatch(content, /MEMORY_FIELDS/);
  assert.equal(truncated, false);
});

test('lone CR line breaks count as lines, as they do on the server', async () => {
  // 服务端按 `splitlines(keepends=True)` 数行，单独一个 `\r` 也是一行结束。用
  // `split('\n')` 数会把整个文件当成 1 行，于是跳过全文读取，第 1998–2000 行的
  // 正文示例被当成尾注删掉，截断还报成 false。
  //
  // The server counts lines the way `splitlines(keepends=True)` does, where a lone
  // `\r` also ends a line. Counting with `split('\n')` reads the whole file as 1 line,
  // skips the full read, strips the body example at lines 1998–2000 and reports the
  // read as complete.
  const lines = Array.from({ length: 1997 }, (_, i) => `line ${i + 1}`);
  lines.push(
    '<!-- MEMORY_FIELDS',
    '{"example": true}',
    '-->',
    'after the example',
  );
  const { content, truncated } = await previewOf(MEMORY, lines.join('\r'));

  assert.equal(serverSplitLines(content).length, 2000);
  assert.match(content, /<!-- MEMORY_FIELDS/);
  assert.equal(truncated, true);
});

test('a file of exactly the cap keeps its last newline', async () => {
  // 2000 行、每行以 `\n` 结尾的文件在 `split('\n')` 下会多出一个末尾空串，被当成
  // 第 2001 行——于是报成截断，还丢掉最后的换行符。
  //
  // A file of 2000 lines each ending in `\n` gains a trailing empty element under
  // `split('\n')`, which looks like a 2001st line — so the read reports truncation and
  // drops the final newline.
  const body = `${'line\n'.repeat(1999)}line\n`;
  const { content, truncated } = await previewOf(MEMORY, body);

  assert.equal(content, body);
  assert.equal(truncated, false);
});

test('whitespace after the trailer follows Python, not JS', async () => {
  // 尾注后是不是「只剩空白」，按 Python 的空白集合判断。JS 的 `trim()` 不认 NEL
  // 和 \x1c–\x1e，会把这种尾注当成正文留在预览里；反过来它认 BOM，会把正文末尾
  // 的 BOM 当空白而连正文一起删掉。
  //
  // Whether only whitespace follows the trailer is decided by Python's whitespace set.
  // JS's `trim()` omits NEL and \x1c–\x1e, so such a trailer would stay in the preview;
  // and it includes BOM, so a body ending in one would be deleted along with it.
  for (const separator of ['\u0085', '\u001c', '\u001d', '\u001e']) {
    const body = `# Body\n\n<!-- MEMORY_FIELDS\n{}\n-->${separator}`;
    const { content } = await previewOf(MEMORY, body);
    assert.equal(content, '# Body', `separator ${JSON.stringify(separator)}`);
  }

  const withBom = '# Body\n\n<!-- MEMORY_FIELDS\n{}\n-->\uFEFF';
  const { content } = await previewOf(MEMORY, withBom);
  assert.equal(content, withBom);
});

test('a letter right after MEMORY_FIELDS is not a reserved marker', async () => {
  // 保留标记要求 `MEMORY_FIELDS` 后面不紧跟单词字符。JS 的 `\b` 只认 ASCII，会把
  // `é`、`中` 当成非单词字符，于是把正文里的 `<!-- MEMORY_FIELDSé ... -->` 删掉；
  // Python 的 `\b` 认 Unicode 字母，不会删。
  //
  // The reserved marker requires no word character right after `MEMORY_FIELDS`. JS's
  // `\b` is ASCII-only, so it treats `é` and `中` as non-word characters and deletes a
  // body comment like `<!-- MEMORY_FIELDSé ... -->`; Python's `\b` knows Unicode
  // letters and leaves it alone.
  for (const letter of ['é', '中', 'Ⅻ', '⑤']) {
    const body = `# Body\n<!-- MEMORY_FIELDS${letter} {} -->`;
    const { content } = await previewOf(MEMORY, body);
    assert.equal(content, body, `letter ${letter}`);
  }

  // 下划线属于单词字符，同样不是保留标记。
  // The underscore is a word character too, so it is not a reserved marker either.
  const underscored = '# Body\n<!-- MEMORY_FIELDS_ {} -->';
  assert.equal((await previewOf(MEMORY, underscored)).content, underscored);
});
