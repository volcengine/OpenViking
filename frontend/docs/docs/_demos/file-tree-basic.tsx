import { useState } from 'react';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { useLang } from '@rspress/core/runtime';
import {
  resolveFileTreeInfoKey,
  resolveInfoCopy,
  showsInlineDescription,
} from '@openviking/react/components/file-tree';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '@openviking/react/components/file-tree';
import {
  ChevronRightIcon,
  FolderIcon,
  FolderOpenIcon,
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
  en: {
    loading: 'Loading…',
    loadMore: 'Load more',
    empty: 'This folder is empty',
    expand: 'Expand',
    collapse: 'Collapse',
    experience: 'Contains an experience board',
    wiki: 'Open the Wiki board',
    conflict: 'Pending conflicts',
  },
  zh: {
    loading: '加载中…',
    loadMore: '加载更多',
    empty: '该文件夹为空',
    expand: '展开',
    collapse: '收起',
    experience: '含经验看板',
    wiki: '打开 Wiki 看板',
    conflict: '存在待确认冲突',
  },
};

/** Host copy the built-in directory descriptions are overridden with. */
const INFO_LABELS = {
  en: undefined,
  zh: {
    description: {
      userRoot: '当前用户的上下文数据',
      memories: '跨会话沉淀的长期记忆',
      peers: '按应用或工作区隔离上下文',
      privacy: '密钥等敏感配置',
      userResources: '用户的私有知识资料',
      sessions: '对话与任务运行记录',
      skills: 'Agent 可用的技能与工具',
      sharedResources: '数据库内的共享知识资料',
      agentRoot: 'Agent 的能力、工具和经验',
      systemRoot: '当前 Account 的内置技能、官方模板与自定义模板',
    },
  },
} as const;

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

const MUTED = { color: '#8f959e' } as const;
const INDENT = 20;
const CHEVRON_SIZE = 16;
const ROW = {
  height: 'var(--ov-ui-file-tree-row-height, 32px)',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  paddingRight: 8,
  borderRadius: 'var(--ov-ui-file-tree-row-radius, 4px)',
} as const;
/** Guide through one ancestor level; the 1px bleed joins consecutive rows. */
const GUIDE = {
  position: 'absolute',
  top: -1,
  bottom: -1,
  width: 1,
  background: 'var(--ov-ui-file-tree-guide, #e5e6eb)',
  pointerEvents: 'none',
} as const;
const CENTER = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
} as const;
const ICON = {
  ...CENTER,
  color: 'var(--ov-ui-file-tree-icon, #3f3f51)',
  width: 16,
  height: 16,
} as const;
const FOLDER = { position: 'relative', width: 14, height: 14 } as const;
const LAYER = {
  ...FOLDER,
  position: 'absolute',
  inset: 0,
  transition: 'opacity 220ms',
} as const;
const BARE_BUTTON = {
  padding: 0,
  border: 0,
  background: 'none',
  cursor: 'pointer',
} as const;
const CHEVRON = { ...CENTER, ...BARE_BUTTON, width: 16, height: 16 } as const;
const INDICATOR = {
  ...CENTER,
  ...BARE_BUTTON,
  flex: 'none',
  width: 24,
  height: 24,
  marginLeft: 'auto',
  borderRadius: 4,
} as const;
/**
 * Product icons used by this demo only. They belong to the host's Wiki and
 * experience features, so they stay here instead of the shared icon package.
 */
const BOARD_D =
  'M2 1.5H4.5C4.77614 1.5 5 1.72386 5 2V5.5C5 5.77614 4.77614 6 4.5 6H2C1.72386 6 1.5 5.77614 1.5 5.5V2C1.5 1.72386 1.72386 1.5 2 1.5ZM7.5 1.5H10C10.2761 1.5 10.5 1.72386 10.5 2V3.5C10.5 3.77614 10.2761 4 10 4H7.5C7.22386 4 7 3.77614 7 3.5V2C7 1.72386 7.22386 1.5 7.5 1.5ZM7.5 6H10C10.2761 6 10.5 6.22386 10.5 6.5V10C10.5 10.2761 10.2761 10.5 10 10.5H7.5C7.22386 10.5 7 10.2761 7 10V6.5C7 6.22386 7.22386 6 7.5 6ZM2 8H4.5C4.77614 8 5 8.22386 5 8.5V10C5 10.2761 4.77614 10.5 4.5 10.5H2C1.72386 10.5 1.5 10.2761 1.5 10V8.5C1.5 8.22386 1.72386 8 2 8Z';
const CONFLICT_STACK_D =
  'M2 1.5H4.5C4.77614 1.5 5 1.72386 5 2V5.5C5 5.77614 4.77614 6 4.5 6H2C1.72386 6 1.5 5.77614 1.5 5.5V2C1.5 1.72386 1.72386 1.5 2 1.5ZM7.5 1.5H10C10.2761 1.5 10.5 1.72386 10.5 2V3.5C10.5 3.77614 10.2761 4 10 4H7.5C7.22386 4 7 3.77614 7 3.5V2C7 1.72386 7.22386 1.5 7.5 1.5ZM7.5 6H10C10.2761 6 10.5 6.22386 10.5 6.5V10C10.5 10.2761 10.2761 10.5 10 10.5H7.5C7.22386 10.5 7 10.2761 7 10V6.5C7 6.22386 7.22386 6 7.5 6ZM2 8H4.5C4.77614 8 5 8.22386 5 8.5V10C5 10.2761 4.77614 10.5 4.5 10.5H2C1.72386 10.5 1.5 10.2761 1.5 10V8.5C1.5 8.22386 1.72386 8 2 8Z';
const CONFLICT_BADGE_D =
  'M4.5 8C4.5 5.79086 6.29086 4 8.5 4V4C10.7091 4 12.5 5.79086 12.5 8V8C12.5 10.2091 10.7091 12 8.5 12H4.65842C4.57093 12 4.5 11.9291 4.5 11.8416V8Z';
const CONFLICT_MARK_D =
  'M8.12525 5.16248C8.3636 5.05726 8.63591 5.0573 8.87427 5.16248C9.03672 5.23421 9.15691 5.36705 9.26587 5.5199C9.37554 5.67377 9.49733 5.88273 9.64771 6.141L11.2776 8.9408C11.4279 9.19896 11.5506 9.40737 11.6301 9.5785C11.7091 9.7484 11.7651 9.91933 11.7463 10.0961C11.7189 10.3552 11.5817 10.5903 11.3704 10.7426C11.2265 10.8461 11.0499 10.8827 10.8626 10.8998C10.6738 10.9169 10.4313 10.9164 10.1311 10.9164H6.86939C6.56887 10.9164 6.32581 10.917 6.13697 10.8998C5.94954 10.8827 5.77304 10.8462 5.62916 10.7426C5.4178 10.5902 5.28064 10.3551 5.25318 10.0961C5.23446 9.91932 5.29137 9.74839 5.37037 9.5785C5.44995 9.40742 5.57168 9.19884 5.72193 8.9408L7.35279 6.14002C7.50292 5.88219 7.62412 5.67363 7.73365 5.5199C7.84252 5.36711 7.96286 5.23426 8.12525 5.16248Z';
const CONFLICT_DOT_D =
  'M8.24986 8.33335V7.00001C8.24986 6.86199 8.36185 6.75009 8.49986 6.75001C8.63793 6.75001 8.74986 6.86194 8.74986 7.00001V8.33335C8.74986 8.47142 8.63793 8.58335 8.49986 8.58335C8.36185 8.58327 8.24986 8.47137 8.24986 8.33335ZM8.76711 9.36655C8.76711 9.50443 8.65532 9.61628 8.51744 9.61655L8.48358 9.61688C8.41725 9.61696 8.35345 9.59049 8.3065 9.54363C8.25961 9.49678 8.23329 9.43317 8.23326 9.36688V9.33335C8.23326 9.19528 8.34519 9.08335 8.48326 9.08335H8.51711C8.65516 9.08337 8.76711 9.19529 8.76711 9.33335V9.36655Z';
const BOOK_D =
  'M8.05556 7.55556C8.05556 6.31099 8.05556 5.68871 8.29776 5.21335C8.51082 4.79522 8.85077 4.45526 9.26891 4.24221C9.74427 4 10.3666 4 11.6111 4H11.8333C12.4556 4 12.7668 4 13.0044 4.1211C13.2135 4.22763 13.3835 4.39761 13.49 4.60668C13.6111 4.84436 13.6111 5.1555 13.6111 5.77778V10.5556C13.6111 11.1778 13.6111 11.489 13.49 11.7267C13.3835 11.9357 13.2135 12.1057 13.0044 12.2122C12.7668 12.3333 12.4556 12.3333 11.8333 12.3333H11.0695C10.3738 12.3333 10.026 12.3333 9.71022 12.4292C9.43069 12.5141 9.17064 12.6533 8.94494 12.8388C8.69001 13.0483 8.49705 13.3378 8.11114 13.9166L8.05556 14L7.99997 13.9166C7.61406 13.3378 7.4211 13.0483 7.16617 12.8388C6.94048 12.6533 6.68043 12.5141 6.40089 12.4292C6.08514 12.3333 5.73728 12.3333 5.04157 12.3333H4.27778C3.6555 12.3333 3.34436 12.3333 3.10668 12.2122C2.89761 12.1057 2.72763 11.9357 2.6211 11.7267C2.5 11.489 2.5 11.1778 2.5 10.5556V5.77778C2.5 5.1555 2.5 4.84436 2.6211 4.60668C2.72763 4.39761 2.89761 4.22763 3.10668 4.1211C3.34436 4 3.6555 4 4.27778 4H4.5C5.74456 4 6.36684 4 6.8422 4.24221C7.26034 4.45526 7.6003 4.79522 7.81335 5.21335C8.05556 5.68871 8.05556 6.31099 8.05556 7.55556ZM8.05556 14V7.55556';
const EXPERIENCE_D = [
  'M5.25 1.75H2.33333C2.01117 1.75 1.75 2.01117 1.75 2.33333V6.41667C1.75 6.73883 2.01117 7 2.33333 7H5.25C5.57217 7 5.83333 6.73883 5.83333 6.41667V2.33333C5.83333 2.01117 5.57217 1.75 5.25 1.75Z',
  'M11.6667 1.75H8.75C8.42783 1.75 8.16667 2.01117 8.16667 2.33333V4.08333C8.16667 4.4055 8.42783 4.66667 8.75 4.66667H11.6667C11.9888 4.66667 12.25 4.4055 12.25 4.08333V2.33333C12.25 2.01117 11.9888 1.75 11.6667 1.75Z',
  'M11.6667 7H8.75C8.42783 7 8.16667 7.26117 8.16667 7.58333V11.6667C8.16667 11.9888 8.42783 12.25 8.75 12.25H11.6667C11.9888 12.25 12.25 11.9888 12.25 11.6667V7.58333C12.25 7.26117 11.9888 7 11.6667 7Z',
  'M5.25 9.33333H2.33333C2.01117 9.33333 1.75 9.5945 1.75 9.91667V11.6667C1.75 11.9888 2.01117 12.25 2.33333 12.25H5.25C5.57217 12.25 5.83333 11.9888 5.83333 11.6667V9.91667C5.83333 9.5945 5.57217 9.33333 5.25 9.33333Z',
];

type IconSvgProps = ComponentPropsWithoutRef<'svg'> & {
  size?: number;
  children?: ReactNode;
};

function Svg({ size = 12, children, ...rest }: IconSvgProps) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 12 12"
      fill="none"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Wiki board: a green grid of panels. */
function WikiBoardIcon(props: IconSvgProps) {
  return (
    <Svg {...props}>
      <path
        d={BOARD_D}
        stroke="currentColor"
        strokeWidth=".8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Svg>
  );
}

/** Wiki root: an open book. */
function WikiBookIcon(props: IconSvgProps) {
  return (
    <Svg {...props} size={16} viewBox="0 0 16 16">
      <path
        d={BOOK_D}
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Svg>
  );
}

/** Wiki board with pending conflicts: orange, with an exclamation badge. */
function WikiConflictIcon(props: IconSvgProps) {
  return (
    <Svg {...props}>
      <path
        d={CONFLICT_STACK_D}
        stroke="#eb7d00"
        strokeWidth=".8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d={CONFLICT_BADGE_D} fill="#eb7d00" />
      <path d={CONFLICT_MARK_D} fill="#fff" />
      <path d={CONFLICT_DOT_D} fill="#fff" />
    </Svg>
  );
}

/** Agent experience: a four-panel board. */
function ExperienceBoardIcon(props: IconSvgProps) {
  return (
    <Svg {...props} viewBox="0 0 14 14">
      {EXPERIENCE_D.map((d) => (
        <path
          key={d}
          d={d}
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </Svg>
  );
}

const BOX = {
  font: '13px/20px system-ui, sans-serif',
  border: '1px solid #e5e6eb',
  borderRadius: 6,
  padding: 8,
  maxHeight: 420,
  overflow: 'auto',
} as const;

export default function FileTreeBasic() {
  const lang = useLang() === 'zh' ? 'zh' : 'en';
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
  const [selected, setSelected] = useState<string>();

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
    <div style={BOX}>
      {rows.map((row) => {
        const pad = 8 + row.depth * INDENT;
        if (row.kind !== 'entry') {
          const busy =
            row.kind === 'loading' || loading.has(row.parentId ?? '');
          return (
            <div
              key={row.id}
              style={{
                ...ROW,
                ...MUTED,
                position: 'relative',
                paddingLeft: pad + INDENT,
              }}
            >
              {/* State rows continue their parent's guide so the line stays unbroken. */}
              {Array.from({ length: row.depth }, (_, level) => (
                <span
                  key={level}
                  aria-hidden
                  style={{
                    ...GUIDE,
                    left: 8 + level * INDENT + CHEVRON_SIZE / 2,
                  }}
                />
              ))}
              {row.kind === 'load-more' ? (
                <button
                  type="button"
                  className="ov-file-tree__action"
                  disabled={busy}
                  onClick={() => row.parentId && loadPage(row.parentId)}
                >
                  {busy ? t.loading : t.loadMore}
                </button>
              ) : row.kind === 'loading' ? (
                t.loading
              ) : (
                t.empty
              )}
            </div>
          );
        }
        const key = resolveFileTreeInfoKey(row.uri);
        return (
          <EntryRow
            key={row.id}
            row={row}
            isOpen={open.has(row.uri)}
            isSelected={selected === row.uri}
            description={
              // The row's own description wins, including an explicit empty
              // string; the semantic copy is only the fallback.
              row.description ??
              (key && showsInlineDescription(key)
                ? resolveInfoCopy(key, INFO_LABELS[lang]).description
                : undefined)
            }
            indicator={INDICATORS[row.uri]}
            text={t}
            onToggle={() => toggle(row)}
            onSelect={() => {
              setSelected(row.uri);
              // 与宿主一致：点击行只展开，收起靠箭头。
              if (row.type === 'directory' && !open.has(row.uri)) toggle(row);
            }}
          />
        );
      })}
    </div>
  );
}

function EntryRow({
  row,
  isOpen,
  isSelected,
  description,
  indicator,
  text,
  onToggle,
  onSelect,
}: {
  row: FileTreeEntryRow;
  isOpen: boolean;
  isSelected: boolean;
  description?: string;
  indicator?: Indicator;
  text: (typeof COPY)['en'];
  onToggle: () => void;
  onSelect: () => void;
}) {
  const isWikiRoot = row.type === 'directory' && row.uri === WIKI;
  return (
    <div
      role="treeitem"
      tabIndex={0}
      aria-selected={isSelected}
      aria-expanded={row.hasChildren ? isOpen : undefined}
      data-selected={isSelected || undefined}
      className="ov-file-tree__row"
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onSelect();
      }}
      style={{
        ...ROW,
        position: 'relative',
        paddingLeft: 8 + row.depth * INDENT,
        cursor: 'pointer',
      }}
    >
      {/* One guide per ancestor level, aligned to that level's chevron centre. */}
      {Array.from({ length: row.depth }, (_, level) => (
        <span
          key={level}
          aria-hidden
          style={{
            ...GUIDE,
            left: 8 + level * INDENT + CHEVRON_SIZE / 2,
          }}
        />
      ))}
      {row.hasChildren ? (
        <button
          type="button"
          aria-label={`${isOpen ? text.collapse : text.expand} ${row.name}`}
          onClick={(event) => {
            event.stopPropagation();
            onToggle();
          }}
          style={CHEVRON}
        >
          <ChevronRightIcon
            width={12}
            height={12}
            style={{
              transform: isOpen ? 'rotate(90deg)' : undefined,
              transition: 'transform 150ms',
            }}
          />
        </button>
      ) : (
        <span style={{ width: 16 }} />
      )}
      <span aria-hidden={isWikiRoot ? undefined : true} style={ICON}>
        {isWikiRoot ? (
          // A Wiki root keeps its book icon in both states; it never shows folders.
          <WikiBookIcon
            role="img"
            aria-label="Wiki"
            width={16}
            height={16}
            style={{ color: ICON.color }}
          />
        ) : row.type === 'directory' ? (
          <span style={FOLDER}>
            <FolderIcon style={{ ...LAYER, opacity: isOpen ? 0 : 1 }} />
            <FolderOpenIcon style={{ ...LAYER, opacity: isOpen ? 1 : 0 }} />
          </span>
        ) : (
          <FileTypeIcon name={row.name} />
        )}
      </span>
      <span>{row.name}</span>
      {description ? <span style={MUTED}>{description}</span> : null}
      {indicator ? <Indicator indicator={indicator} text={text} /> : null}
    </div>
  );
}

/**
 * The row-end indicator. A Wiki root turns orange with an exclamation mark while
 * it has pending conflicts; otherwise both entries use their board icon.
 */
function Indicator({
  indicator,
  text,
}: {
  indicator: Indicator;
  text: (typeof COPY)['en'];
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
