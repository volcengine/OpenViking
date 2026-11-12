/**
 * 搜索的纯逻辑：请求体、结果归一、筛选可用性与目录候选。不碰 React、不碰请求，
 * 也**不能有任何运行时导入**——测试直接加载本文件，`.js` 后缀在那里解析不了。
 *
 * Pure logic of the search view: bodies, result normalisation, filter availability and
 * scope options. It touches neither React nor the network, and takes **no runtime
 * imports**: tests load this file directly, where a `.js` suffix does not resolve.
 */

import type {
  FilterAvailability,
  ScopeOption,
  SearchFilters,
  SearchHit,
  SearchMode,
  SearchResult,
} from './search-types.js';

/** 语义检索的两种模式。The two semantic modes. */
const SEMANTIC: readonly SearchMode[] = ['find', 'search'];

/** 某个模式是否为语义检索。Whether the mode is a semantic one. */
export const isSemantic = (mode: SearchMode): boolean =>
  SEMANTIC.includes(mode);

/**
 * 各模式能用的筛选。目录在三态里区分多选与单选：语义检索接受 URI 数组，
 * grep/glob 只接受单个起始 URI。
 *
 * Which filters each mode supports. The scope tri-state separates multi-select from
 * single: semantic retrieval takes a URI array, while grep/glob take one start URI.
 */
export const FILTER_AVAILABILITY: Record<SearchMode, FilterAvailability> = {
  find: { scope: 'multi', levels: true, time: true, caseInsensitive: false },
  search: { scope: 'multi', levels: true, time: true, caseInsensitive: false },
  grep: { scope: 'single', levels: false, time: false, caseInsensitive: true },
  glob: { scope: 'single', levels: false, time: false, caseInsensitive: false },
};

/**
 * 把筛选归一到目标模式实际会用的形态。
 *
 * 单选模式只认一个起始 URI，多选模式认数组。切换模式时若不收敛，`scopes` 会留下
 * 两项，而单选控件只能勾中其中一个、请求也只取第一个——界面显示与真正发出的范围
 * 不一致。归一化放在切换处，显示与请求就消费同一个值。
 *
 * Normalises the filters to what the target mode actually uses.
 *
 * Single-select modes take one start URI, multi-select modes take an array. Without
 * collapsing on a mode switch, `scopes` keeps two entries while the single-select control
 * can show only one and the request takes the first — the display and the range actually
 * sent disagree. Normalising at the switch makes both consume the same value.
 */
export function normalizeFilters(
  filters: SearchFilters,
  mode: SearchMode,
): SearchFilters {
  const a = FILTER_AVAILABILITY[mode];
  // 层级与时间在 grep/glob 下不生效，一并清掉，避免切回来时出现用户没再选过的
  // 旧条件。
  //
  // Levels and time do not apply to grep/glob, so they clear too — otherwise a switch
  // back would restore conditions the user has not re-chosen.
  return {
    ...filters,
    scopes: a.scope === 'single' ? filters.scopes.slice(0, 1) : filters.scopes,
    levels: a.levels ? filters.levels : [],
    since: a.time ? filters.since : '',
    until: a.time ? filters.until : '',
    caseInsensitive: a.caseInsensitive ? filters.caseInsensitive : false,
  };
}

/** 每类结果最多返回多少条。Cap per result category. */
const LIMIT = 20;

/**
 * 用户根用 `~` 别名，不写死具体用户名：服务端在请求边界按认证身份展开，
 * 写死 `user/default` 在换账号之后就指错了人。
 *
 * The user root uses the `~` alias rather than a literal user name: the server
 * expands it against the authenticated identity, while a hard-coded `user/default`
 * points at the wrong user as soon as the account changes.
 */
const HOME = 'viking://~';

/** 服务端可检索的根，按设计稿的顺序。Roots the server can retrieve, in design order. */
const ALL_SCOPES: readonly ScopeOption[] = [
  { uri: `${HOME}/resources` },
  { uri: 'viking://resources' },
  { uri: `${HOME}/skills` },
  { uri: 'viking://agent/skills' },
];

/** 一个根是否覆盖另一个。Whether one root covers another. */
const covers = (root: string, uri: string): boolean =>
  uri === root || uri.startsWith(`${root}/`);

/**
 * 按当前根算出可勾选的目录。
 *
 * `rootUri` 是 `viking://` 时给出全部四项。
 *
 * 更窄的根给出**它自己**，而不是覆盖它的祖先：宿主把浏览范围收敛到
 * `viking://resources/project-a` 时，候选若是祖先 `viking://resources`，勾选后
 * `target_uri` 就从项目根换成了整个资源库，检索范围被悄悄放大，返回的兄弟目录
 * 资源树里也定位不到。只有根本身不是可检索根（例如它落在 `viking://agent` 下
 * 却不在 `skills` 里）时，才退回到覆盖它的那一项。
 *
 * Resolves the selectable scopes for the current root.
 *
 * A `viking://` root yields all four.
 *
 * A narrower root yields **itself** rather than the scope that covers it: when a host
 * narrows browsing to `viking://resources/project-a`, offering the ancestor
 * `viking://resources` would swap `target_uri` from the project root to the whole
 * library on selection — silently widening the search, and returning sibling resources
 * the tree cannot locate. Only a root that is not itself retrievable (it sits under
 * `viking://agent` but outside `skills`) falls back to the scope covering it.
 */
export function buildScopeOptions(rootUri: string): ScopeOption[] {
  if (!rootUri || rootUri === 'viking://') return [...ALL_SCOPES];
  if (ALL_SCOPES.some((option) => covers(option.uri, rootUri))) {
    return [{ uri: rootUri }];
  }
  const kept = ALL_SCOPES.filter((option) => covers(option.uri, rootUri));
  return kept.length > 0 ? kept : [];
}

/**
 * 把筛选落到请求体上。语义检索带数组与层级、时间；grep/glob 只带单个起始 URI。
 *
 * Applies the filters to a request body. Semantic modes carry arrays plus level and
 * time; grep/glob carry a single start URI.
 */
function withFilters(
  mode: SearchMode,
  filters: SearchFilters,
  rootUri: string,
): Record<string, unknown> {
  const availability = FILTER_AVAILABILITY[mode];
  if (isSemantic(mode)) {
    const target = filters.scopes.length > 0 ? filters.scopes : rootUri;
    return {
      target_uri: target,
      ...(filters.levels.length > 0 ? { level: filters.levels } : {}),
      ...(filters.since ? { since: filters.since } : {}),
      ...(filters.until ? { until: filters.until } : {}),
      ...(filters.since || filters.until
        ? { time_field: filters.timeField }
        : {}),
    };
  }
  return {
    uri: filters.scopes[0] ?? rootUri,
    ...(availability.caseInsensitive
      ? { case_insensitive: filters.caseInsensitive }
      : {}),
  };
}

/**
 * 一个模式的一次请求体。语义检索用 `query`，grep/glob 用 `pattern`。
 *
 * 路径不在这里给：这个模块要在不加载构建产物的测试里直接跑，引入 `client/paths`
 * 会带来 `.js` 后缀的运行时导入。路径由取数层按模式映射。
 *
 * The body of one request for one mode. Semantic modes send `query`; grep/glob send
 * `pattern`.
 *
 * The path is not part of it: this module runs directly from source in tests that load
 * no build output, and importing `client/paths` would bring a `.js` runtime import with
 * it. The loading layer maps the mode to its path.
 */
export function buildSearchBody(
  mode: SearchMode,
  query: string,
  filters: SearchFilters,
  rootUri: string,
): Record<string, unknown> {
  const trimmed = query.trim();
  const shared = withFilters(mode, filters, rootUri);
  if (isSemantic(mode)) {
    return { query: trimmed, limit: LIMIT, ...shared };
  }
  return {
    pattern: mode === 'glob' ? normalizeGlobPattern(trimmed) : trimmed,
    node_limit: LIMIT,
    ...shared,
  };
}

/**
 * 不含 `/` 的模式在任意深度匹配（隐式补 `&#42;&#42;/` 前缀）；含 `/` 的视为已经锚定。
 * 这与 gitignore、VS Code 的 files-to-include 以及 fd 的约定一致。
 *
 * A pattern without `/` matches at any depth (implicit double-star prefix); one that
 * contains `/` is treated as already anchored — matching gitignore, VS Code's
 * files-to-include and fd.
 */
export function normalizeGlobPattern(pattern: string): string {
  const trimmed = pattern.trim();
  return trimmed.includes('/') ? trimmed : `**/${trimmed}`;
}

/** 服务端语义检索返回的一条。One hit from the server's semantic search. */
interface RawHit {
  uri?: string;
  abstract?: string;
  context_type?: string;
  level?: number;
  score?: number;
}

/** 语义检索的分组响应。The grouped response of a semantic search. */
export interface RawFindResult {
  memories?: RawHit[];
  resources?: RawHit[];
  skills?: RawHit[];
}

const asHit = (
  raw: RawHit,
  fallback: SearchHit['contextType'],
): SearchHit | null => {
  const uri = raw.uri;
  if (!uri) return null;
  const type = raw.context_type;
  return {
    uri,
    contextType:
      type === 'memory' || type === 'resource' || type === 'skill'
        ? type
        : fallback,
    abstract: raw.abstract?.trim() ?? '',
    score: typeof raw.score === 'number' ? raw.score : 0,
    level: typeof raw.level === 'number' ? raw.level : 2,
  };
};

const group = (
  raw: RawHit[] | undefined,
  fallback: SearchHit['contextType'],
): SearchHit[] =>
  (raw ?? [])
    .map((item) => asHit(item, fallback))
    .filter((hit): hit is SearchHit => hit !== null);

/**
 * 把语义检索的分组结果归一。三组保持分组——搜索视图只需要「跳到哪个资源」，
 * 但保留分组让调用方仍能按类型做后续处理。
 *
 * Normalises a grouped semantic result. The three groups stay separate: the search
 * view only needs "which resource to jump to", but keeping them lets a caller still
 * act per type.
 */
export function normalizeFindResult(raw: RawFindResult): SearchResult {
  const memories = group(raw.memories, 'memory');
  const resources = group(raw.resources, 'resource');
  const skills = group(raw.skills, 'skill');
  return {
    memories,
    resources,
    skills,
    total: memories.length + resources.length + skills.length,
  };
}

/** grep 的一条命中。One grep match. */
interface RawGrepMatch {
  uri?: string;
  line?: number;
  content?: string;
}

/**
 * 把 grep 的 `matches` 归一。命中行内容进 `abstract`，行号单独保留——结果行
 * 把行号接在文件名后面，摘要位显示命中内容。
 *
 * Normalises grep's `matches`. The matched line becomes the abstract and the line
 * number is kept apart: the row appends the number to the file name and shows the
 * matched content in the abstract slot.
 */
export function normalizeGrepResult(matches: unknown): SearchResult {
  const list = Array.isArray(matches) ? (matches as RawGrepMatch[]) : [];
  const resources = list
    .filter((match) => typeof match?.uri === 'string')
    .map<SearchHit>((match) => ({
      uri: match.uri as string,
      contextType: 'resource',
      abstract: typeof match.content === 'string' ? match.content.trim() : '',
      resultKind: 'grep',
      line: typeof match.line === 'number' ? match.line : undefined,
      score: 0,
      level: 2,
    }));
  return { memories: [], resources, skills: [], total: resources.length };
}

/**
 * 把 glob 的 `matches` 归一。返回的是 URI 字符串数组，没有摘要。
 *
 * 目录命中带尾斜杠——`glob` 返回的是扁平字符串列表，尾斜杠是调用方区分目录与
 * 文件的唯一标记（`_glob_match_uri` 在服务端就是这么加的）。但 `ls` 与树节点 ID
 * 不带尾斜杠，原样保存会让「跳到哪个资源」指向一个树里不存在的 id：点击结果后
 * 当前 URI 是 `docs/`，而已加载的 `docs` 行仍是未选中。归一化时去掉尾斜杠，目录
 * 信息保留在 `isDir` 上，两个用途各取所需。
 *
 * Normalises glob's `matches`. It returns bare URI strings, so there is no abstract.
 *
 * A directory match carries a trailing slash — the flat string list makes that the only
 * way a caller can tell a directory from a file (the server's `_glob_match_uri` adds it).
 * But `ls` and tree node ids carry none, so keeping it would make "which resource to jump
 * to" point at an id the tree does not have: after a click the current URI is `docs/`
 * while the loaded `docs` row stays unselected. Normalisation strips it and keeps the
 * directory fact on `isDir`, so each consumer gets what it needs.
 */
export function normalizeGlobResult(matches: unknown): SearchResult {
  const list = Array.isArray(matches) ? matches : [];
  const resources = list
    .filter((uri): uri is string => typeof uri === 'string')
    .map<SearchHit>((uri) => ({
      uri: uri.replace(/\/+$/, '') || uri,
      isDir: uri.endsWith('/'),
      contextType: 'resource',
      abstract: '',
      resultKind: 'glob',
      score: 0,
      level: 2,
    }));
  return { memories: [], resources, skills: [], total: resources.length };
}

/**
 * 三组按顺序展平成一个列表。
 *
 * 语义检索按 URI 去重——同一个资源可能同时出现在两组里，搜索视图只需要「跳到哪个
 * 资源」，重复行只会让人以为有两个文件。
 *
 * grep 不去重：同一文件的多行命中是**不同的结果**，各带自己的行号与命中内容。
 * 按 URI 合并会把第 2 行之后的命中全部吞掉，用户会以为该文件只匹配了一次。
 *
 * Flattens the three groups into one list.
 *
 * Semantic hits deduplicate by URI: one resource can appear in two groups, and the view
 * only needs "which resource to jump to" — a duplicate row reads as two files.
 *
 * grep hits do not deduplicate: several matches in one file are **distinct results**,
 * each with its own line number and matched text. Merging by URI would swallow every
 * match after the first and make the file look like it matched once.
 */
export function flattenResult(result: SearchResult | null): SearchHit[] {
  if (!result) return [];
  const byUri = new Map<string, SearchHit>();
  const grepHits: SearchHit[] = [];
  for (const hit of [
    ...result.resources,
    ...result.memories,
    ...result.skills,
  ]) {
    if (hit.resultKind === 'grep') {
      grepHits.push(hit);
      continue;
    }
    const existing = byUri.get(hit.uri);
    // 同一个 URI 出现在两组里时保留有摘要的那条：摘要是行里唯一有信息量的
    // 副文本，丢掉它这一行就只剩文件名。
    //
    // When one URI appears in two groups, keep the entry that carries an abstract: the
    // abstract is the only informative secondary text in the row, and dropping it
    // leaves just the file name.
    if (!existing) byUri.set(hit.uri, hit);
    else if (!existing.abstract && hit.abstract) byUri.set(hit.uri, hit);
  }
  return [...byUri.values(), ...grepHits];
}
