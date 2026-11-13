/**
 * 离线示例里三种检索方式的模拟应答：语义检索、grep 与 glob。
 *
 * 从 `file-browser-basic-data.ts` 拆出来，那个文件同时放示例目录、文件正文和
 * 请求分发，加进三种模式后超过了单文件行数上限。
 *
 * 这些都是示例替身，不是产品实现：换成真实服务时删掉本文件，由 Provider 指向
 * 实际地址即可。
 *
 * Simulated answers for the demo's three retrieval kinds: semantic search, grep and
 * glob.
 *
 * Split out of `file-browser-basic-data.ts`, which holds the demo directory, file
 * bodies and request dispatch together and went past the per-file line cap once the
 * three modes were added.
 *
 * All of it is a stand-in, not product code: drop this file when pointing the demo at a
 * real service through the Provider.
 */

/** 一条目录项的形状，与 `fs/ls` 的返回一致。One directory entry, shaped like `fs/ls`. */
interface Entry {
  uri: string;
}

/** 语义检索的分组结果。Grouped result of a semantic search. */
interface FindPayload {
  memories?: Entry[];
  resources?: Entry[];
}

/** grep 的一条命中。One grep match. */
interface GrepMatch {
  uri: string;
  line: number;
  content: string;
}

/** 搜索失败时的统一应答。The uniform failure response. */
export function searchFailure() {
  return new Response(
    JSON.stringify({
      status: 'error',
      error: { code: 'DEMO_SEARCH_FAILED', message: 'Demo search unavailable' },
    }),
    { status: 503, headers: { 'content-type': 'application/json' } },
  );
}

/**
 * 按查询词在全部条目里做子串匹配，模拟语义检索的返回形状。
 *
 * Substring matching over every entry, in the shape of the semantic search.
 */
export function semanticSearch(
  query: string,
  entries: readonly Entry[],
  userRoot: string,
): FindPayload {
  const needle = query.trim().toLowerCase();
  if (!needle) return {};
  const hits = entries.filter((entry) =>
    entry.uri.toLowerCase().includes(needle),
  );
  return {
    memories: hits.filter((entry) => entry.uri.startsWith(userRoot)),
    resources: hits.filter((entry) =>
      entry.uri.startsWith('viking://resources'),
    ),
  };
}

/**
 * 把查询词当正则跑一遍示例文件，返回 grep 形状的命中：每个文件一行，带行号。
 * 正则非法时返回空数组，与真实接口的 422 相比这里选择静默——示例不该因为一个
 * 打错的正则就报错。
 *
 * Runs the query as a regular expression over the demo files, returning grep-shaped
 * hits: one row per file with its line number. An invalid pattern yields nothing; the
 * real API answers 422, but a demo should not fail because of a mistyped expression.
 */
export function grepFiles(pattern: string, files: Record<string, string>) {
  let regex: RegExp;
  try {
    regex = new RegExp(pattern, 'i');
  } catch {
    return [];
  }
  const matches: GrepMatch[] = [];
  for (const [uri, content] of Object.entries(files)) {
    content.split('\n').forEach((text, index) => {
      if (regex.test(text))
        matches.push({ uri, line: index + 1, content: text.trim() });
    });
  }
  return matches;
}

/**
 * 把 glob 模式转成正则，在示例 URI 上做匹配，返回裸 URI 字符串，与真实接口一致。
 *
 * 逐字符扫描而不是连续替换：`**` 与 `*` 的替换顺序一旦错位，`**` 会被拆成两个
 * `[^/]*`，跨目录的模式就匹配不到了。
 *
 * Turns the glob pattern into a regular expression and matches it against the demo
 * URIs, returning bare strings as the real endpoint does.
 *
 * It scans character by character rather than chaining replacements: once `**` and `*`
 * are substituted in the wrong order, `**` becomes two `[^/]*` and no pattern can cross
 * a directory.
 */
export function globFiles(pattern: string, files: Record<string, string>) {
  let source = '^';
  for (let index = 0; index < pattern.length; index++) {
    const char = pattern[index] ?? '';
    if (char === '*' && pattern[index + 1] === '*') {
      index += 1;
      if (pattern[index + 1] === '/') {
        index += 1;
        source += '(?:.*/)?';
      } else {
        source += '.*';
      }
    } else if (char === '*') {
      source += '[^/]*';
    } else if (char === '?') {
      source += '.';
    } else {
      source += char.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
  }
  let regex: RegExp;
  try {
    regex = new RegExp(`${source}$`);
  } catch {
    return [];
  }
  return Object.keys(files).filter((uri) => regex.test(uri));
}
