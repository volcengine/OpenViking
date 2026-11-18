import { Button } from '../../shared/primitives/button/button.js';
import { useState } from 'react';

import type {
  CommandConsoleResourceTreeContent,
  CommandConsoleResourceTreeItem,
} from './types.js';
import {
  CommandConsoleUriLink,
  CommandConsoleUriText,
} from './command-console-uri-text.js';

const PAGE_SIZE = 20;

export interface CommandConsoleResourceTreeProps {
  content: CommandConsoleResourceTreeContent;
  onOpenUri?: (uri: string) => void;
  loadMoreLabel: string;
  noResultLabel: string;
  resourceLabel: string;
}

function TreeIdentity({
  item,
  onOpenUri,
}: {
  item: CommandConsoleResourceTreeItem;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      <span className="ov-command-console__tree-title">
        {item.uri ? (
          <CommandConsoleUriLink uri={item.uri} onOpenUri={onOpenUri}>
            {item.title}
          </CommandConsoleUriLink>
        ) : (
          <CommandConsoleUriText
            text={item.title}
            mode="literal"
            onOpenUri={onOpenUri}
          />
        )}
      </span>
      {item.metaLabel ? (
        <span className="ov-command-console__tree-meta">
          <CommandConsoleUriText
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
export function CommandConsoleResourceTree({
  content,
  onOpenUri,
  loadMoreLabel,
  noResultLabel,
  resourceLabel,
}: CommandConsoleResourceTreeProps) {
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
    <div className="ov-command-console__tree">
      {content.title ? (
        <div className="ov-command-console__card-title">
          <CommandConsoleUriText
            text={content.title}
            mode="literal"
            onOpenUri={onOpenUri}
          />
        </div>
      ) : null}
      {!content.items.length ? (
        <div className="ov-command-console__no-result">{noResultLabel}</div>
      ) : null}
      {columns.length ? (
        <div className="ov-command-console__table-frame">
          <table className="ov-command-console__table">
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
                        <CommandConsoleUriText
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
        <div className="ov-command-console__tree-items">
          {visibleItems.map((item) => (
            <div key={item.id} className="ov-command-console__tree-item">
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
          className="ov-command-console__load-more"
          onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
        >
          {loadMoreLabel} ({hiddenCount})
        </Button>
      ) : null}
    </div>
  );
}
