/**
 * 记忆文件末尾的 `MEMORY_FIELDS` 注释：服务端写给系统的内部元数据（版本、来源
 * 追踪 id、记忆类型），不属于正文，也不该出现在预览里。
 *
 * 正常读取（`content/read` 不带 `raw`）由服务端先剥掉它再按 offset/limit 切片，
 * 这里的处理是同一规则的兜底：宿主可能通过自定义传输层或网关让请求带上
 * `raw=true`，那条路径返回的是原始字节，尾注会原样回来。
 *
 * 以服务端尾注语义为基础，而不是「看到同形注释就删」：
 *
 * - 只认**位于文件末尾**的那一块（其后只剩空白），正文中间出现的同形注释属于
 *   内容本身，例如资源文档里举例的这段标记；
 * - 连续多块尾注一起剥掉；Markdown 正文中独占一行的未闭合尾注隐藏到文件末尾；
 * - Markdown 代码围栏中的示例保留；
 * - 只在记忆命名空间生效，资源与技能文件不经过这一步。
 *
 * 空白与「单词字符」都按 Python 的语义判定，不沿用 JS 的 `\s` / `\b` / `trim()`：
 * 两者集合并不相同，照搬会在默认 `raw=false` 路径上删掉服务端已经保留的正文。
 * 详见下面两个字符集的说明。
 *
 * The `MEMORY_FIELDS` comment at the end of a memory file: internal metadata the
 * server writes for its own bookkeeping (version, trace ids, memory type). It is not
 * part of the body and must not reach the preview.
 *
 * A normal read (`content/read` without `raw`) has the server strip it before slicing
 * by offset/limit; the handling here is the same rule as a fallback. A host can make
 * the request carry `raw=true` through a custom transport or a gateway, and that path
 * returns the stored bytes with the trailer intact.
 *
 * The rule mirrors the backend rather than "delete anything shaped like this":
 *
 * - only the block that ends the file counts (nothing but whitespace after it); a
 *   same-shaped comment in the middle is content, such as one quoted in a resource
 *   document;
 * - consecutive trailing blocks are all removed;
 * - it applies to the memory namespace only — resource and skill files never go
 *   through it.
 *
 * Whitespace and "word characters" follow Python's definitions rather than JS's
 * `\s` / `\b` / `trim()`. The two sets differ, and copying JS's would delete body text
 * the server deliberately kept on the default `raw=false` path. Both sets are spelled
 * out below.
 */

/**
 * Python `\s` / `str.strip()` 认定的空白字符。与 JS 的 `\s` 有两处方向相反的差异：
 *
 * - JS 不认 `\x1c`–`\x1f`（文件/组/记录/单元分隔符）和 `\x85`（NEL），Python 认；
 * - JS 认 U+FEFF（BOM），Python 不认。
 *
 * 照搬 JS 的 `\s`，前者会让「尾注后跟 NEL」不被识别，后者会把「正文末尾的 BOM」
 * 当成尾注前的空白而删掉正文。
 *
 * The whitespace Python's `\s` and `str.strip()` recognise. Two differences from JS's
 * `\s` run in opposite directions:
 *
 * - JS omits `\x1c`–`\x1f` (file/group/record/unit separators) and `\x85` (NEL);
 *   Python includes them;
 * - JS includes U+FEFF (BOM); Python does not.
 *
 * Copying JS's `\s` would miss a trailer followed by NEL, and would treat a BOM after
 * the body as separator whitespace and delete that body.
 */
import { isScalar, parseAllDocuments, visit } from 'yaml';
import { isQuotedComment, startsCommentLine } from './fs-comment-trailers.js';

const PY_WHITESPACE_CODES = [
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0,
  0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
];

/** 上面的码位组成的字符串，用于 `includes` 与正则字符类。
 *  The same code points as a string, for `includes` and a regex character class. */
const PY_WHITESPACE = String.fromCharCode(...PY_WHITESPACE_CODES);

/**
 * Python `\w` 的集合：Unicode 的字母、数字与下划线。JS 的 `\w` 只有 ASCII，`\b`
 * 因此把 `é`、`中` 这类字母当成非单词字符——`<!-- MEMORY_FIELDSé {} -->` 会被
 * 误判为保留标记而删掉，而 Python 后端不会。
 *
 * 用 `\p{L}` / `\p{N}` 表达 Unicode 字母与数字，加上下划线，正好等价于 Python 的
 * `\w`（逐码位比对过：0 处不一致）。
 *
 * The set Python's `\w` covers: Unicode letters, numbers and the underscore. JS's `\w`
 * is ASCII-only, so its `\b` treats `é` or `中` as a non-word character —
 * `<!-- MEMORY_FIELDSé {} -->` would be taken for a reserved marker and deleted, while
 * the Python backend leaves it alone.
 *
 * `\p{L}` and `\p{N}` for Unicode letters and numbers plus the underscore is exactly
 * Python's `\w` (compared code point by code point: no difference).
 */
const PY_WORD = '\\p{L}\\p{N}_';

/**
 * 一个保留注释：`<!--`、空白、`MEMORY_FIELDS`、**非单词字符**、任意内容、`-->`。
 *
 * 第三个分组要求 `MEMORY_FIELDS` 后面不能紧跟单词字符，对应 Python 正则里的
 * `MEMORY_FIELDS\b`；用前瞻表达，避免依赖 JS 的 ASCII `\b`。
 *
 * A reserved comment: `<!--`, whitespace, `MEMORY_FIELDS`, a **non-word** character,
 * followed by its payload; the closing delimiter is located separately.
 *
 * The third group requires no word character right after `MEMORY_FIELDS`, which is what
 * `MEMORY_FIELDS\b` means in the Python pattern; a lookahead expresses it without
 * relying on JS's ASCII-only `\b`.
 */
const MEMORY_FIELDS_COMMENT = new RegExp(
  `<!--[${PY_WHITESPACE}]*MEMORY_FIELDS(?![${PY_WORD}])`,
  'gu',
);

/** 文本是否只由 Python 认定的空白组成。Whether the text is nothing but Python whitespace. */
function isPythonBlank(text: string): boolean {
  for (const char of text) {
    if (!PY_WHITESPACE.includes(char)) return false;
  }
  return true;
}

/** 去掉结尾的 Python 空白。Drops the trailing Python whitespace. */
function trimPythonEnd(text: string): string {
  let end = text.length;
  while (end > 0 && PY_WHITESPACE.includes(text[end - 1] ?? '')) end--;
  return text.slice(0, end);
}

/** 拆分 Viking URI 的路径段，忽略查询串与末尾斜杠。Path segments of a Viking URI. */
function uriParts(uri: string): string[] {
  const [beforeQuery = ''] = uri.split('?');
  const path = beforeQuery.replace(/\/+$/, '');
  if (!path.startsWith('viking://')) return [];
  return path.slice('viking://'.length).split('/').filter(Boolean);
}

/**
 * 该 URI 是否落在记忆命名空间。`viking://~/memories/...` 这类当前用户别名按
 * `user/<uid>` 展开后再判断，与请求边界上的解析一致。
 *
 * Whether the URI sits in the memory namespace. The `viking://~/memories/...` home
 * alias is expanded to `user/<uid>` first, matching how the request boundary resolves
 * it.
 */
export function isMemoryUri(uri: string): boolean {
  const parts = uriParts(uri);
  if (parts.length < 3) return false;
  if (parts[0] !== 'user' && parts[0] !== '~') return false;
  // 别名少一段 uid，补上后路径位置才与规范 URI 对齐。
  // The alias omits the uid segment, so insert it to line up with a canonical URI.
  const scoped = parts[0] === '~' ? ['user', '~', ...parts.slice(1)] : parts;
  if (scoped[2] === 'memories') return true;
  return (
    scoped.length >= 5 && scoped[2] === 'peers' && scoped[4] === 'memories'
  );
}

/**
 * 剥掉文件末尾的 `MEMORY_FIELDS` 尾注；没有尾注时原样返回。
 *
 * 每次循环只认「其后全是空白」的最后一块，剥完再判断一次，因为连续多块尾注
 * 需要逐个处理；正文中间的同形注释因此不会被误删。
 *
 * Removes the trailing `MEMORY_FIELDS` blocks; returns the text unchanged when there
 * is none.
 *
 * Each pass takes only the last block that nothing but whitespace follows, then checks
 * again, because consecutive trailing blocks need one pass each; a same-shaped comment
 * in the middle of the body is therefore never removed.
 */
export function stripMemoryFieldsTrailer(
  text: string,
  format: 'markdown' | 'yaml' | 'text' = 'text',
): string {
  let scalars: [number, number][] | undefined;
  let visible = text;
  for (;;) {
    let start = -1;
    MEMORY_FIELDS_COMMENT.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = MEMORY_FIELDS_COMMENT.exec(visible)) !== null) {
      const offset = match.index;
      const close = visible.indexOf('-->', match.index + match[0].length);
      if (
        close < 0 &&
        (format !== 'markdown' || !startsCommentLine(visible, match.index))
      )
        continue;
      const after = close < 0 ? '' : visible.slice(close + 3);
      if (!isPythonBlank(after)) continue;
      const quoted =
        format === 'yaml'
          ? (scalars ??= yamlScalarRanges(text)).some(
              ([start, end]) => start <= offset && close + 3 <= end,
            )
          : isQuotedComment(visible, match.index);
      if (!quoted) {
        start = match.index;
        break;
      }
    }
    if (start < 0) return visible;
    visible = trimPythonEnd(visible.slice(0, start));
  }
}

/** Find YAML values without reserializing; error-recovery nodes cannot protect trailers. */
function yamlScalarRanges(text: string): [number, number][] {
  const ranges: [number, number][] = [];
  // Lone CR becomes LF without changing offsets into the original source.
  for (const doc of parseAllDocuments(text.replace(/\r(?!\n)/g, '\n'), {
    prettyErrors: false,
  })) {
    visit(doc, (_, node) => {
      if (!isScalar(node) || !node.range) return;
      const [start, end] = node.range;
      const invalid = doc.errors.some(
        ({ pos: [a, b] }) => a < end && Math.max(a + 1, b) > start,
      );
      if (!invalid) ranges.push([start, end]);
    });
  }
  return ranges;
}
