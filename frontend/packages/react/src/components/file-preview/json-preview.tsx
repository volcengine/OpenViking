import { useEffect, useMemo, useState } from 'react';
import type { ContentRendererProps } from '@ark-design/ui/file-viewer/core';
import { resolveJsonPreviewLabels } from './file-preview-labels.js';
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
  // useEffect 必要性：自定义渲染器需在 DOM 提交后把就绪状态和空句柄同步给 Ark FileViewer；
  // 渲染阶段调用会更新父组件，且没有可替代的声明式接口。回调不建立订阅或资源，依赖变化时
  // 重新上报即可，卸载时无需清理。
  useEffect(() => {
    onLoadingChange?.(null);
    onViewerStateChange({ viewMode: 'preview' });
    registerHandle(null);
  }, [onLoadingChange, onViewerStateChange, registerHandle]);
  const lines = useMemo(
    () => visibleJsonPreviewLines(model, collapsed),
    [collapsed, model],
  );
  const labels = resolveJsonPreviewLabels(locale);
  const action = (expanded: boolean, lineNumber: number) =>
    expanded ? labels.collapseLine(lineNumber) : labels.expandLine(lineNumber);

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
