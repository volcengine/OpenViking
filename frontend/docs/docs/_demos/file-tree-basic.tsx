import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FileTree } from '@openviking/react/components/file-tree';
import type { FileTreeRow } from '@openviking/react/components/file-tree';
import {
  ExperienceBoardIcon,
  WikiBoardIcon,
  WikiConflictBoardIcon as WikiConflictIcon,
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '@openviking/react/icons';
import '@openviking/react/components/file-tree/style.css';

/** `path: children`; a trailing `/` marks a directory, `null` marks a file. */
interface Spec {
  [name: string]: Spec | null;
}
interface Node {
  uri: string;
  name: string;
  type: 'directory' | 'file';
  children?: Node[];
}

const USER = 'viking://user/alice';
const MEMORIES = `${USER}/memories`;
const WIKI = 'viking://resources/project-wiki';
const ARCHIVE = 'viking://resources/archive';
const PAGE = 3;

const SPEC: Spec = {
  [`${USER}/`]: {
    'memories/': {
      'profile.md': null,
      'identity.md': null,
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

const TREE = entries(SPEC);
const BY_URI = new Map<string, Node>();
const walk = (nodes: Node[]) =>
  nodes.forEach((n) => {
    BY_URI.set(n.uri, n);
    if (n.children) walk(n.children);
  });
walk(TREE);
const childrenOf = (uri: string) => BY_URI.get(uri)?.children ?? [];

/** Row-end indicator: the Agent experience board, or a Wiki root with its conflict state. */
type Indicator =
  { kind: 'experience' } | { kind: 'wiki'; pendingConflicts: boolean };

const INDICATORS: Record<string, Indicator> = {
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
const COPY = {
  'en-US': {
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
function flatten(nodes: Node[], depth: number, s: State): FileTreeRow[] {
  return nodes.flatMap((node) => {
    const isDir = node.type === 'directory';
    const isOpen = isDir && s.open.has(node.uri);
    const row: FileTreeRow = {
      ...node,
      kind: 'entry',
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

const CENTER = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
} as const;
const BARE_BUTTON = {
  padding: 0,
  border: 0,
  background: 'none',
  cursor: 'pointer',
} as const;
const INDICATOR = {
  ...CENTER,
  ...BARE_BUTTON,
  flex: 'none',
  width: 24,
  height: 24,
  marginLeft: 'auto',
  borderRadius: 4,
} as const;
export default function FileTreeBasic() {
  const lang = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const t = COPY[lang];
  // Directories expanded on first paint already carry their first page, so the
  // initial view shows content instead of an empty or loading row.
  const [open, setOpen] = useState<ReadonlySet<string>>(
    new Set([USER, MEMORIES]),
  );
  const [loaded, setLoaded] = useState<Record<string, Node[]>>(() => ({
    [USER]: childrenOf(USER).slice(0, PAGE),
    [MEMORIES]: childrenOf(MEMORIES).slice(0, PAGE),
  }));
  const [loading, setLoading] = useState<ReadonlySet<string>>(new Set());
  // 默认选中一行，进页面就能看到选中态，不必先点一下。
  const [selected, setSelected] = useState<string>(`${MEMORIES}/profile.md`);

  /** Fetches one more page of children, showing the loading row meanwhile. */
  const loadPage = (uri: string) => {
    if (loading.has(uri)) return;
    setLoading((p) => new Set(p).add(uri));
    window.setTimeout(() => {
      setLoaded((p) => ({
        ...p,
        [uri]: childrenOf(uri).slice(0, (p[uri]?.length ?? 0) + PAGE),
      }));
      setLoading((p) => {
        const next = new Set(p);
        next.delete(uri);
        return next;
      });
    }, 400);
  };

  /** Opening fetches the first page; collapsing only hides rows. */
  const toggle = (node: Node) => {
    const next = new Set(open);
    if (!next.delete(node.uri)) {
      next.add(node.uri);
      if (!loaded[node.uri] && childrenOf(node.uri).length) loadPage(node.uri);
    }
    setOpen(next);
  };

  const rows = flatten(TREE, 0, { open, loaded, loading });

  return (
    <div style={{ height: 420 }}>
      <FileTree
        locale={lang}
        rows={rows}
        selectedId={selected}
        aria-label={t.treeLabel}
        operations={{
          select: (row) => {
            setSelected(row.uri);
            // 与宿主一致：点击行只展开，收起靠箭头。
            if (row.type === 'directory' && !open.has(row.uri)) toggle(row);
            return Promise.resolve();
          },
          toggleExpand: (row) => {
            toggle(row);
            return Promise.resolve();
          },
          loadMore: (row) => {
            if (row.parentId) loadPage(row.parentId);
            return Promise.resolve();
          },
        }}
        renderRowIcon={(row) => <FileTypeIcon name={row.name} />}
        renderDashboard={(row) => {
          const indicator = INDICATORS[row.uri];
          return indicator ? (
            <Indicator indicator={indicator} text={t} />
          ) : null;
        }}
      />
    </div>
  );
}

function Indicator({
  indicator,
  text,
}: {
  indicator: Indicator;
  text: (typeof COPY)['en-US'];
}) {
  const pending = indicator.kind === 'wiki' && indicator.pendingConflicts;
  const label =
    indicator.kind === 'experience'
      ? text.experience
      : pending
        ? text.conflict
        : text.wiki;
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={(event) => event.stopPropagation()}
      style={{ ...INDICATOR, color: pending ? '#eb7d00' : '#0b9e6b' }}
    >
      {indicator.kind === 'experience' ? (
        <ExperienceBoardIcon width={12} height={12} aria-hidden />
      ) : pending ? (
        <WikiConflictIcon width={12} height={12} aria-hidden />
      ) : (
        <WikiBoardIcon width={12} height={12} aria-hidden />
      )}
    </button>
  );
}

/** Renders the file icon the icon set maps to this name, in its brand colour. */
function FileTypeIcon({ name }: { name: string }) {
  const Icon = getFileTypeIcon(name);
  return (
    <Icon
      aria-hidden="true"
      focusable="false"
      width={16}
      height={16}
      style={{ color: getFileTypeColor(resolveFileTypeIconKey(name)) }}
    />
  );
}
