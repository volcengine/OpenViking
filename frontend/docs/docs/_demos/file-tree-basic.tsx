import { Checkbox, Select, SelectOption } from '#demo-controls';

import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FileTree } from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';
import {
  USER,
  MEMORIES,
  PAGE,
  TREE,
  COPY,
  INDICATORS,
  childrenOf,
  flatten,
  type Node,
} from '#file-tree-basic-data';
import { FileTypeIcon, Indicator } from '#file-tree-basic-icons';

export default function FileTreeBasic() {
  const lang = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const t = COPY[lang];
  const [failRefresh, setFailRefresh] = useState(false);
  const [externalLoading, setExternalLoading] = useState(false);
  const [notice, setNotice] =
    useState<
      keyof Pick<
        typeof t,
        'idle' | 'searched' | 'refreshing' | 'refreshed' | 'failed'
      >
    >('idle');
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
    <div>
      <div
        style={{
          display: 'flex',
          gap: 16,
          flexWrap: 'wrap',
          alignItems: 'center',
          marginBottom: 12,
        }}
      >
        <label>
          {t.caseLabel}{' '}
          <Select
            value={[failRefresh ? 'failure' : 'success']}
            onChange={([value]) => {
              if (value !== undefined) setFailRefresh(value === 'failure');
            }}
          >
            <SelectOption value="success">{t.successCase}</SelectOption>
            <SelectOption value="failure">{t.failureCase}</SelectOption>
          </Select>
        </label>
        <Checkbox
          checked={externalLoading}
          onChange={(next) => setExternalLoading(next)}
        >
          {' '}
          {t.externalLoading}
        </Checkbox>
      </div>
      <div style={{ height: 420 }}>
        <FileTree
          locale={lang}
          rows={rows}
          selectedId={selected}
          loading={externalLoading}
          onSearch={() => setNotice('searched')}
          onRefresh={() => setNotice('refreshed')}
          aria-label={t.treeLabel}
          operations={{
            search: async () => {},
            refresh: async () => {
              setNotice('refreshing');
              await new Promise((resolve) => window.setTimeout(resolve, 800));
              if (failRefresh) {
                setNotice('failed');
                throw new Error('Demo refresh failure');
              }
            },
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
          renderRowTrailing={(row) => {
            const indicator = INDICATORS[row.uri];
            return indicator ? (
              <Indicator indicator={indicator} text={t} />
            ) : null;
          }}
        />
      </div>
      <p role={notice === 'failed' ? 'alert' : 'status'}>{t[notice]}</p>
    </div>
  );
}
