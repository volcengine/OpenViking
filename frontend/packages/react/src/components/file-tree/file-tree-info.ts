import type { FileTreeInfoKey } from './types.js';

/**
 * 只在浮层里出现的键。这些目录的名称本身已经说明用途，行内再挂一行说明会挤掉
 * 同级节点，因此行内只显示名称。
 *
 * Keys shown in the tooltip only. These directory names already say what they
 * are, so an inline description would crowd out siblings; the row shows only the
 * name.
 */
const TOOLTIP_ONLY_KEYS = new Set<FileTreeInfoKey>([
  'profile',
  'preferences',
  'entities',
  'events',
  'identity',
  'soul',
  'cases',
  'trajectories',
  'experiences',
]);

/** 顶层根段的别名，按路径首段判定。Root segment aliases, matched on the first segment. */
const ROOT_KEY_BY_SEGMENT: Record<string, FileTreeInfoKey> = {
  agent: 'agentRoot',
  agents: 'agentRoot',
  memory: 'memoryRoot',
  memories: 'memoryRoot',
  resource: 'resourceRoot',
  resources: 'resourceRoot',
  session: 'sessionRoot',
  sessions: 'sessionRoot',
  system: 'systemRoot',
};

/** 用户作用域下的目录，按 `/user/{id}/` 后的第三段判定。 */
const USER_SCOPE_KEY_BY_SEGMENT: Record<string, FileTreeInfoKey> = {
  memories: 'memories',
  peers: 'peers',
  privacy: 'privacy',
  resources: 'userResources',
  sessions: 'sessions',
  skills: 'skills',
};

/** Agent 根下的目录，按 `viking://agent/` 后的第二段判定。 */
const AGENT_KEY_BY_SEGMENT: Record<string, FileTreeInfoKey> = {
  endpoints: 'agentEndpoints',
  payments: 'agentPayments',
  skills: 'agentSkills',
  tools: 'agentTools',
};

/** 长期记忆下的目录，按 `/user/{id}/memories/` 后的第四段判定。 */
const MEMORY_KEY_BY_SEGMENT: Record<string, FileTreeInfoKey> = {
  'profile.md': 'profile',
  preferences: 'preferences',
  entities: 'entities',
  events: 'events',
  'identity.md': 'identity',
  'soul.md': 'soul',
  cases: 'cases',
  trajectories: 'trajectories',
  experiences: 'experiences',
};

const USER_SEGMENTS = new Set(['user', 'users']);

/**
 * 把 `viking://` 地址拆成小写路径段，忽略协议、查询串与末尾斜杠。
 *
 * Splits a `viking://` address into lower-cased segments, ignoring the scheme,
 * query string and trailing slashes.
 */
export function splitVikingUri(uri: string): string[] {
  const withoutScheme = uri.replace(/^viking:\/*/i, '');
  const withoutQuery = withoutScheme.split(/[?#]/)[0] ?? '';

  return withoutQuery
    .split('/')
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => segment.toLowerCase());
}

/**
 * 按路径结构判定目录的语义键，判定不出来时返回 `null`。
 *
 * 只认路径形状，不认节点类型：同名目录在目录与文件位置上含义相同，由调用方
 * 决定是否使用。判定规则与产品无关，任何宿主都能直接命中。
 *
 * Resolves the semantic key of a directory from its path structure, or `null`
 * when nothing matches.
 *
 * Only the path shape is inspected, not the node type: a name means the same
 * thing whether it is a directory or a file, and the caller decides whether to
 * use it. The rules are product-neutral, so any host matches them.
 */
export function resolveFileTreeInfoKey(uri: string): FileTreeInfoKey | null {
  const segments = splitVikingUri(uri);
  const first = segments[0];

  if (!first) return null;

  if (USER_SEGMENTS.has(first)) {
    // 用户根：`viking://user` 与 `viking://user/{id}` 都指向同一个作用域。
    // User root: both `viking://user` and `viking://user/{id}` name that scope.
    if (segments.length <= 2) return 'userRoot';

    const third = segments[2];
    if (!third) return null;

    if (segments.length === 3) return USER_SCOPE_KEY_BY_SEGMENT[third] ?? null;

    // 只有 `memories` 的子级有语义；其他分支下的同名目录沿用上级含义。
    // Only children of `memories` carry a meaning of their own.
    if (third !== 'memories' || segments.length !== 4) return null;

    const fourth = segments[3];

    return fourth ? (MEMORY_KEY_BY_SEGMENT[fourth] ?? null) : null;
  }

  if (segments.length === 1) {
    if (first === 'resources') return 'sharedResources';

    return ROOT_KEY_BY_SEGMENT[first] ?? null;
  }

  // Agent 根的子目录有各自的含义；更深的层级不再细分，沿用「无」。
  // Children of the agent root carry their own meaning; deeper levels are not
  // distinguished and resolve to none.
  if ((first === 'agent' || first === 'agents') && segments.length === 2) {
    const second = segments[1];

    return second ? (AGENT_KEY_BY_SEGMENT[second] ?? null) : null;
  }

  return null;
}

/**
 * 该键的说明是否显示在行内。判定不出来时为 `false`。
 *
 * Whether the description of this key renders inline; `false` when nothing
 * matched.
 */
export function showsInlineDescription(key: FileTreeInfoKey | null): boolean {
  return key !== null && !TOOLTIP_ONLY_KEYS.has(key);
}
