import { useEffect, useMemo, useState } from 'react';
import type { ContentRendererProps } from '@ark-design/ui/file-viewer/core';
import {
  visibleJsonPreviewLines,
  type JsonPreviewModel,
} from './json-preview-model.js';

export function JsonPreview({
  fileName,
  locale,
  model,
  onLoadingChange,
  onViewerStateChange,
  registerHandle,
}: Pick<
  ContentRendererProps,
  | 'fileName'
  | 'locale'
  | 'onLoadingChange'
  | 'onViewerStateChange'
  | 'registerHandle'
> & {
  model: JsonPreviewModel;
}) {
  const [collapsed, setCollapsed] = useState(
    () => new Set(model.defaultCollapsed),
  );
  useEffect(() => setCollapsed(new Set(model.defaultCollapsed)), [model]);
  useEffect(() => {
    onLoadingChange?.(null);
    onViewerStateChange({ viewMode: 'preview' });
    registerHandle(null);
  }, [onLoadingChange, onViewerStateChange, registerHandle]);
  const lines = useMemo(
    () => visibleJsonPreviewLines(model, collapsed),
    [collapsed, model],
  );
  const action = (expanded: boolean, lineNumber: number) =>
    locale === 'zh-CN'
      ? `${expanded ? '折叠' : '展开'}第 ${lineNumber} 行`
      : `${expanded ? 'Collapse' : 'Expand'} line ${lineNumber}`;

  return (
    <div
      aria-label={fileName}
      className="ov-file-preview__json"
      data-file-preview-json="true"
      role="region"
    >
      {lines.map((line) => {
        const index = line.lineNumber - 1;
        const foldable = line.closeLine !== undefined;
        const isCollapsed = collapsed.has(index);
        return (
          <div
            className="ov-file-preview__json-line"
            data-line={line.lineNumber}
            key={line.lineNumber}
          >
            <span aria-hidden="true" className="ov-file-preview__json-gutter">
              {line.lineNumber}
            </span>
            {foldable ? (
              <button
                aria-label={action(!isCollapsed, line.lineNumber)}
                aria-expanded={!isCollapsed}
                className="ov-file-preview__json-toggle"
                onClick={() =>
                  setCollapsed((current) => {
                    const next = new Set(current);
                    if (next.has(index)) next.delete(index);
                    else next.add(index);
                    return next;
                  })
                }
                type="button"
              >
                <span aria-hidden="true">›</span>
              </button>
            ) : (
              <span className="ov-file-preview__json-toggle-spacer" />
            )}
            <code className="ov-file-preview__json-code">
              {isCollapsed ? line.collapsedText : line.text || ' '}
            </code>
          </div>
        );
      })}
    </div>
  );
}
