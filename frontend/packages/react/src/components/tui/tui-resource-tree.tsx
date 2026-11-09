import { useState } from 'react';

import type { TuiResourceTreeContent } from './types.js';
import { TuiUriText } from './tui-uri-text.js';

const PAGE_SIZE = 20;

export interface TuiResourceTreeProps {
  content: TuiResourceTreeContent;
  onOpenUri?: (uri: string) => void;
  loadMoreLabel: string;
  noResultLabel: string;
}

/** 资源树结果：有明细时按列对齐，否则逐行列出。 */
export function TuiResourceTree({
  content,
  onOpenUri,
  loadMoreLabel,
  noResultLabel,
}: TuiResourceTreeProps) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const columns = Array.from(
    new Set(
      content.items.flatMap(
        (item) => item.detailRows?.map((row) => row.label) ?? [],
      ),
    ),
  );
  const visibleItems = content.items.slice(0, visibleCount);
  const hiddenCount = content.items.length - visibleItems.length;

  return (
    <div className="ov-tui__tree">
      {content.title ? (
        <div className="ov-tui__card-title">
          <TuiUriText text={content.title} onOpenUri={onOpenUri} />
        </div>
      ) : null}
      {!content.items.length ? (
        <div className="ov-tui__no-result">{noResultLabel}</div>
      ) : null}
      {columns.length ? (
        <div className="ov-tui__table-frame">
          <table className="ov-tui__table">
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((item) => (
                <tr key={item.id}>
                  {columns.map((column) => {
                    const value =
                      item.detailRows?.find((row) => row.label === column)
                        ?.value ?? '';
                    return (
                      <td key={column}>
                        <TuiUriText text={value} onOpenUri={onOpenUri} />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ov-tui__tree-items">
          {visibleItems.map((item) => {
            const body = (
              <>
                <span className="ov-tui__tree-title">
                  {item.uri && onOpenUri ? (
                    <button
                      type="button"
                      className="ov-tui__link"
                      onClick={() => onOpenUri(item.uri ?? '')}
                    >
                      {item.title}
                    </button>
                  ) : (
                    <TuiUriText text={item.title} onOpenUri={onOpenUri} />
                  )}
                </span>
                {item.metaLabel ? (
                  <span className="ov-tui__tree-meta">
                    <TuiUriText text={item.metaLabel} onOpenUri={onOpenUri} />
                  </span>
                ) : null}
              </>
            );

            return (
              <div key={item.id} className="ov-tui__tree-item">
                {body}
              </div>
            );
          })}
        </div>
      )}
      {hiddenCount > 0 ? (
        <button
          type="button"
          className="ov-tui__load-more"
          onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
        >
          {loadMoreLabel} ({hiddenCount})
        </button>
      ) : null}
    </div>
  );
}
