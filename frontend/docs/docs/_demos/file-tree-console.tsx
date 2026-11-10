import { useRef, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { FileTree } from '@openviking/react/components/file-tree';
import type { FileTreeEntryRow } from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';
import '#file-tree-console.css';
import { COPY, ENTRIES } from '#file-tree-console-data';

export default function FileTreeConsole() {
  const text = COPY[useLang() === 'zh' ? 'zh' : 'en'];
  const [open, setOpen] = useState(
    new Set(['user', 'user/alice', 'user/alice/peers']),
  );
  const [selected, setSelected] = useState('user');
  const [search, setSearch] = useState(true);
  const [refresh, setRefresh] = useState(true);
  const [fail, setFail] = useState(false);
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const description = (row: FileTreeEntryRow) => {
    if (row.id === 'user') return '';
    if (row.id === 'user/alice') return text.user;
    if (row.id === 'resources') return text.shared;
    const key = row.name as keyof typeof text;
    return text[key] ?? '';
  };
  const rows = ENTRIES.flatMap((entry) => {
    const parts = entry.id.split('/');
    if (
      parts
        .slice(0, -1)
        .some((_, index) => !open.has(parts.slice(0, index + 1).join('/')))
    ) {
      return [];
    }
    const row = {
      ...entry,
      expanded: open.has(entry.id),
      description: description(entry),
    };
    if (
      row.expanded &&
      !ENTRIES.some((item) => item.id.startsWith(`${row.id}/`))
    ) {
      return [
        row,
        {
          kind: 'empty' as const,
          id: `${row.id}/empty`,
          parentId: row.id,
          depth: row.depth + 1,
        },
      ];
    }
    return [row];
  });
  const results = ENTRIES.filter((row) =>
    row.id.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const toggle = (id: string) =>
    setOpen((previous) => {
      const next = new Set(previous);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  return (
    <div className="ov-file-tree-demo">
      <div className="ov-file-tree-demo__options">
        <label>
          <input
            type="checkbox"
            checked={search}
            onChange={(event) => setSearch(event.target.checked)}
          />
          {text.searchOption}
        </label>
        <label>
          <input
            type="checkbox"
            checked={refresh}
            onChange={(event) => setRefresh(event.target.checked)}
          />
          {text.refreshOption}
        </label>
        <label>
          <input
            type="checkbox"
            checked={fail}
            onChange={(event) => setFail(event.target.checked)}
          />
          {text.failOption}
        </label>
      </div>
      <div className="ov-file-tree-demo__panel">
        <FileTree
          aria-label={text.tree}
          rows={rows}
          selectedId={selected}
          search={search}
          refresh={refresh}
          labels={{
            search: text.search,
            refresh: text.refresh,
            empty: text.empty,
          }}
          operations={{
            search: () => {
              setQuery('');
              dialog.current?.showModal();
              return Promise.resolve();
            },
            refresh: async () => {
              setNotice('');
              await new Promise((resolve) => window.setTimeout(resolve, 600));
              if (fail) {
                setNotice(text.failed);
                throw new Error('Demo refresh failure');
              }
            },
            select: (row) => {
              setSelected(row.id);
              setOpen((previous) => new Set(previous).add(row.id));
              return Promise.resolve();
            },
            toggleExpand: (row) => {
              toggle(row.id);
              return Promise.resolve();
            },
          }}
          onRefresh={() => setNotice(text.refreshed)}
        />
      </div>
      {notice ? (
        <p role={notice === text.failed ? 'alert' : 'status'}>{notice}</p>
      ) : null}
      <dialog
        ref={dialog}
        className="ov-file-tree-demo__dialog"
        aria-label={text.results}
      >
        <form method="dialog" className="ov-file-tree-demo__dialog-heading">
          <strong>{text.results}</strong>
          <button type="submit">{text.close}</button>
        </form>
        <input
          type="search"
          aria-label={text.placeholder}
          placeholder={text.placeholder}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="ov-file-tree-demo__results">
          {results.length ? (
            results.map((row) => (
              <button
                key={row.id}
                type="button"
                onClick={() => {
                  setSelected(row.id);
                  const parts = row.id.split('/');
                  setOpen(
                    (previous) =>
                      new Set([
                        ...previous,
                        ...parts.map((_, index) =>
                          parts.slice(0, index + 1).join('/'),
                        ),
                      ]),
                  );
                  dialog.current?.close();
                }}
              >
                {row.uri}
              </button>
            ))
          ) : (
            <p>{text.noResults}</p>
          )}
        </div>
      </dialog>
    </div>
  );
}
