import type { FileTreeRow } from '@openviking/react/components/file-tree';

/** `path: children`; a trailing `/` marks a directory, `null` marks a file. */
interface Spec {
  [name: string]: Spec | null;
}
export interface Node {
  uri: string;
  name: string;
  type: 'directory' | 'file';
  children?: Node[];
}

export const USER = 'viking://user/alice';
export const MEMORIES = `${USER}/memories`;
const WIKI = 'viking://resources/project-wiki';
const ARCHIVE = 'viking://resources/archive';
export const PAGE = 3;

const SPEC: Spec = {
  [`${USER}/`]: {
    'memories/': {
      'profile.md': null,
      'soul.md': null,
      'preferences/': {},
      'entities/': {},
      'events/': {},
      'cases/': {},
      'trajectories/': {},
      'experiences/': {},
    },
    'peers/': { 'claude-code/': {}, 'cursor/': {}, 'trae/': {} },
    'privacy/': { 'keys.json': null },
    'resources/': { 'handbook.pdf': null, 'notes.md': null },
    'sessions/': { '2026-11-07.jsonl': null },
    'settings/': { 'user_config.json': null },
    'skills/': { 'wiki-compile/': { 'SKILL.md': null, 'references/': {} } },
    'tools/': { 'mcp.json': null },
  },
  'viking://resources/': {
    'project-wiki/': {
      'entity/': { 'starfield.md': null, 'incident.md': null },
      'method/': { 'release.md': null },
      'index.md': null,
    },
    'archive/': Object.fromEntries(
      Array.from({ length: 9 }, (_, i) => [`service_000${i + 1}.md`, null]),
    ),
    'empty-notes/': {},
  },
  'viking://agent/': { 'skills/': {}, 'tools/': {} },
  'viking://system/': {},
};

function entries(spec: Spec, parent = ''): Node[] {
  return Object.entries(spec).flatMap(([key, value]) => {
    const isDir = key.endsWith('/');
    const path = key.replace(/\/$/, '');
    const uri = path.includes('://') ? path : `${parent}/${path}`;
    const node: Node = {
      uri,
      name: uri.slice(uri.lastIndexOf('/') + 1),
      type: isDir ? 'directory' : 'file',
    };
    if (isDir && value) node.children = entries(value, uri);
    return [node];
  });
}

export const TREE = entries(SPEC);
const BY_URI = new Map<string, Node>();
const walk = (nodes: Node[]) =>
  nodes.forEach((n) => {
    BY_URI.set(n.uri, n);
    if (n.children) walk(n.children);
  });
walk(TREE);
export const childrenOf = (uri: string) => BY_URI.get(uri)?.children ?? [];

/** Row-end indicator: the Agent experience board, or a Wiki root with its conflict state. */
export type Indicator =
  { kind: 'experience' } | { kind: 'wiki'; pendingConflicts: boolean };

export const INDICATORS: Record<string, Indicator> = {
  [`${MEMORIES}/experiences`]: { kind: 'experience' },
  [WIKI]: { kind: 'wiki', pendingConflicts: false },
  [ARCHIVE]: { kind: 'wiki', pendingConflicts: true },
};

/**
 * Demo copy. The docs site renders this demo under both locales, so the strings
 * follow the page language instead of being fixed English.
 *
 * 示例文案。文档站中英文页面共用这份示例，因此文案跟随页面语言。
 */
export const COPY = {
  'en-US': {
    caseLabel: 'Refresh case (simulated)',
    successCase: 'Success',
    failureCase: 'Failure',
    externalLoading: 'Host-controlled loading',
    idle: 'Click search or refresh to see the triggered event.',
    searched: 'onSearch triggered',
    refreshing: 'Simulating refresh…',
    refreshed: 'Refresh succeeded · onRefresh triggered',
    failed: 'Simulated refresh failed. Click refresh to retry.',

    loading: 'Loading…',
    loadMore: 'Load more',
    empty: 'This folder is empty',
    expand: 'Expand',
    collapse: 'Collapse',
    experience: 'Contains an experience board',
    wiki: 'Open the Wiki board',
    conflict: 'Pending conflicts',
    treeLabel: 'File tree',
  },
  'zh-CN': {
    caseLabel: '刷新结果（模拟）',
    successCase: '成功',
    failureCase: '失败',
    externalLoading: '宿主控制 loading',
    idle: '点击搜索或刷新，查看触发事件。',
    searched: 'onSearch 已触发',
    refreshing: '模拟刷新中…',
    refreshed: '刷新成功 · onRefresh 已触发',
    failed: '模拟刷新失败，可点击刷新重试。',

    loading: '加载中…',
    loadMore: '加载更多',
    empty: '该文件夹为空',
    expand: '展开',
    collapse: '收起',
    experience: '含经验看板',
    wiki: '打开 Wiki 看板',
    conflict: '存在待确认冲突',
    treeLabel: '文件树',
  },
};

interface State {
  open: ReadonlySet<string>;
  loaded: Readonly<Record<string, Node[]>>;
  loading: ReadonlySet<string>;
}

/** Host-side flattening: emits loading and load-more rows around paged children. */
export function flatten(nodes: Node[], depth: number, s: State): FileTreeRow[] {
  return nodes.flatMap((node) => {
    const isDir = node.type === 'directory';
    const isOpen = isDir && s.open.has(node.uri);
    const row: FileTreeRow = {
      ...node,
      kind: 'entry',
      levelLabel: isDir ? undefined : 'L0',
      id: node.uri,
      depth,
      expanded: isOpen,
      hasChildren: isDir,
    };
    if (!isOpen) return [row];
    const kids = s.loaded[node.uri] ?? [];
    const total = childrenOf(node.uri).length;
    const busy = s.loading.has(node.uri);
    const meta: FileTreeRow[] = [];
    // A page in flight keeps the rows already loaded and only replaces the tail,
    // so paging appends instead of clearing and re-rendering the subtree.
    if (busy && !kids.length) {
      meta.push({
        kind: 'loading',
        id: `loading:${node.uri}`,
        parentId: node.uri,
        depth: depth + 1,
      });
    } else if (!kids.length) {
      meta.push({
        kind: 'empty',
        id: `empty:${node.uri}`,
        parentId: node.uri,
        depth: depth + 1,
      });
    } else if (kids.length < total) {
      meta.push({
        kind: 'load-more',
        id: `more:${node.uri}`,
        parentId: node.uri,
        depth: depth + 1,
        loading: busy,
      });
    }
    return [row, ...flatten(kids, depth + 1, s), ...meta];
  });
}
