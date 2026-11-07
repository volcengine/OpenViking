import { ContentTabIcon, resolveFileIcon } from './file-preview-icons.js';
import type { FilePreviewTab } from './types.js';

export interface FilePreviewTabRowProps {
  tabs: readonly FilePreviewTab[];
  activeKey: string;
  disabled: boolean;
  onSelect: (key: string) => void;
}

/**
 * 页签行：只显示图标与标题，其余元素都在内容区标题行。
 * The tab row shows only an icon and a title; everything else lives in the
 * content header.
 */
export function FilePreviewTabRow({
  tabs,
  activeKey,
  disabled,
  onSelect,
}: FilePreviewTabRowProps) {
  return (
    <div role="tablist" className="ov-file-preview__tab-row">
      {tabs.map((tab) => {
        const selected = tab.key === activeKey;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={`ov-file-preview-tab-${tab.key}`}
            aria-selected={selected}
            aria-controls={`ov-file-preview-panel-${tab.key}`}
            tabIndex={selected ? 0 : -1}
            disabled={tab.disabled || disabled}
            title={typeof tab.title === 'string' ? tab.title : undefined}
            className={[
              'ov-file-preview__tab',
              selected ? 'ov-file-preview__tab--active' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            onClick={() => onSelect(tab.key)}
          >
            <span className="ov-file-preview__tab-icon" aria-hidden="true">
              {tab.icon ??
                (tab.kind === 'file' ? (
                  resolveFileIcon(tab.source.fileName)
                ) : (
                  <ContentTabIcon />
                ))}
            </span>
            <span className="ov-file-preview__tab-title">
              {tab.title ??
                (tab.kind === 'file' ? tab.source.fileName : tab.key)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
