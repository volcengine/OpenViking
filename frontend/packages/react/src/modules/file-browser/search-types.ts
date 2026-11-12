/**
 * 搜索的模式、筛选与命中结构。四种检索方式（find / search / grep / glob）在
 * 服务端返回的形状不同，这里统一成同一个 `SearchHit`，视图只认这一种。
 *
 * Modes, filters and hits of the search view. The four retrieval modes return
 * different shapes from the server; they normalise into one `SearchHit` here so the
 * view only ever sees one.
 */

/** 检索方式。Retrieval mode. */
export type SearchMode = 'find' | 'search' | 'grep' | 'glob';

/** 模式顺序，与设计稿的下拉一致。Mode order, matching the design's menu. */
export const SEARCH_MODES: readonly SearchMode[] = [
  'find',
  'search',
  'grep',
  'glob',
];

/** 默认模式。Default mode. */
export const DEFAULT_SEARCH_MODE: SearchMode = 'find';

/**
 * 一条归一后的命中。
 *
 * `score` 与 `level` 保留但不渲染：设计稿的结果行只有文件名、URI 与摘要三块，
 * 没有指标位。留着是为了后续要做排序或分层展示时不必回头改请求层。
 *
 * One normalised hit.
 *
 * `score` and `level` are kept but never rendered: the design's result row carries
 * only the name, the URI and the abstract, with no metric slot. They stay so a later
 * sort or level display does not have to reach back into the request layer.
 */
export interface SearchHit {
  uri: string;
  contextType: 'memory' | 'resource' | 'skill';
  abstract: string;
  /** grep/glob 的命中标记；语义检索没有。grep/glob marker; absent for semantic hits. */
  resultKind?: 'grep' | 'glob';
  /** grep 的命中行号。grep's matched line number. */
  line?: number;
  score: number;
  level: number;
}

/** 分组结果，与原生接口的 `{memories, resources, skills}` 同形。 */
export interface SearchResult {
  memories: SearchHit[];
  resources: SearchHit[];
  skills: SearchHit[];
  total: number;
}

/** 高级筛选的取值。Values of the advanced filters. */
export interface SearchFilters {
  /** 选中的目录；空数组表示不限定。Selected scopes; empty means unrestricted. */
  scopes: string[];
  /** 内容层级：0=L0、1=L1、2=L2；空数组表示不限。Content levels; empty means all. */
  levels: number[];
  /** 时间字段。Which timestamp the range applies to. */
  timeField: 'created_at' | 'updated_at';
  /** 起始日期（YYYY-MM-DD）。Start date. */
  since: string;
  /** 结束日期（YYYY-MM-DD）。End date. */
  until: string;
  /** 忽略大小写，仅 grep 支持。Case-insensitive matching, grep only. */
  caseInsensitive: boolean;
}

/** 默认筛选：不限定。Defaults: nothing restricted. */
export const EMPTY_FILTERS: SearchFilters = {
  scopes: [],
  levels: [],
  timeField: 'updated_at',
  since: '',
  until: '',
  caseInsensitive: false,
};

/** 一个可勾选的目录范围。One selectable scope. */
export interface ScopeOption {
  uri: string;
}

/**
 * 各筛选分区在某个模式下的可用性。Availability of each filter group per mode.
 *
 * `scope` 是三态：`multi` 多选、`single` 单选、`none` 不可用。
 */
export interface FilterAvailability {
  scope: 'multi' | 'single' | 'none';
  levels: boolean;
  time: boolean;
  caseInsensitive: boolean;
}
