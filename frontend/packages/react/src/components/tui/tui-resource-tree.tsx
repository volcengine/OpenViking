import { Button } from '../../shared/components/button/button.js';
import { useState } from 'react';

import type { TuiResourceTreeContent, TuiResourceTreeItem } from './types.js';
import { TuiUriLink, TuiUriText } from './tui-uri-text.js';

const PAGE_SIZE = 20;

export interface TuiResourceTreeProps {
  content: TuiResourceTreeContent;
  onOpenUri?: (uri: string) => void;
  loadMoreLabel: string;
  noResultLabel: string;
  resourceLabel: string;
}

function TreeIdentity({
  item,
  onOpenUri,
}: {
  item: TuiResourceTreeItem;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      <span className="ov-tui__tree-title">
        {item.uri ? (
          <TuiUriLink uri={item.uri} onOpenUri={onOpenUri}>
            {item.title}
          </TuiUriLink>
        ) : (
          <TuiUriText text={item.title} mode="literal" onOpenUri={onOpenUri} />
        )}
      </span>
      {item.metaLabel ? (
        <span className="ov-tui__tree-meta">
          <TuiUriText
            text={item.metaLabel}
            mode="prose"
            onOpenUri={onOpenUri}
          />
        </span>
      ) : null}
    </>
  );
}

/** 资源树结果：有明细时按列对齐，否则逐行列出。 */
export function TuiResourceTree({
  content,
  onOpenUri,
  loadMoreLabel,
  noResultLabel,
  resourceLabel,
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
          <TuiUriText
            text={content.title}
            mode="literal"
            onOpenUri={onOpenUri}
          />
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
                <th scope="col">{resourceLabel}</th>
                {columns.map((column) => (
                  <th key={column} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((item) => (
                <tr key={item.id}>
                  <td>
                    <TreeIdentity item={item} onOpenUri={onOpenUri} />
                  </td>
                  {columns.map((column) => {
                    const value =
                      item.detailRows?.find((row) => row.label === column)
                        ?.value ?? '';
                    return (
                      <td key={column}>
                        <TuiUriText
                          text={value}
                          mode="literal"
                          onOpenUri={onOpenUri}
                        />
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
          {visibleItems.map((item) => (
            <div key={item.id} className="ov-tui__tree-item">
              <TreeIdentity item={item} onOpenUri={onOpenUri} />
            </div>
          ))}
        </div>
      )}
      {hiddenCount > 0 ? (
        <Button
          size="small"
          type="default"
          htmlType="button"
          className="ov-tui__load-more"
          onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
        >
          {loadMoreLabel} ({hiddenCount})
        </Button>
      ) : null}
    </div>
  );
}
