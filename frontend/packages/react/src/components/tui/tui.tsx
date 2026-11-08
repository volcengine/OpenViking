import { useCallback, useEffect, useRef, useState } from 'react';

import { TuiAssist } from './tui-assist.js';
import { TuiComposer } from './tui-composer.js';
import { resolveTuiLabels } from './tui-labels.js';
import { TuiRecordItem } from './tui-record.js';
import { useTuiCompletion } from './use-tui-completion.js';
import type { TuiProps } from './types.js';

export type {
  TuiCommandHelpContent,
  TuiCommandHelpItem,
  TuiCommandHelpSection,
  TuiLabels,
  TuiProps,
  TuiRecord,
  TuiRecordContent,
  TuiRecordStatus,
  TuiResourcePreviewContent,
  TuiResourceTreeContent,
  TuiResourceTreeDetailRow,
  TuiResourceTreeItem,
  TuiSearchListContent,
  TuiSearchResultItem,
  TuiStatusContent,
  TuiStatusRow,
  TuiTextContent,
  TuiWriteResultContent,
} from './types.js';

/**
 * 命令记录、结果与输入区。宿主负责执行与会话，组件只渲染传入的记录，并在
 * 成功操作后回调。
 *
 * Command records, results and input. The host owns execution and sessions; the
 * component renders the records it is given and reports successful operations.
 */
export function Tui({
  records,
  value,
  disabled = false,
  emptyState,
  onValueChange,
  onSubmit,
  onOpenUri,
  onCopy,
  labels,
  className,
  ...rest
}: TuiProps) {
  const text = resolveTuiLabels(labels);
  const bodyRef = useRef<HTMLDivElement>(null);
  const readOnly = !onValueChange;
  const completion = useTuiCompletion(value, !disabled && !readOnly);
  const [activeIndex, setActiveIndex] = useState(0);
  const setActiveIndexSafely = useCallback(
    (next: number | ((index: number) => number)) => {
      setActiveIndex((index) =>
        typeof next === 'function' ? next(index) : next,
      );
    },
    [],
  );

  useEffect(() => {
    const body = bodyRef.current;
    if (body) body.scrollTop = body.scrollHeight;
  }, [records]);

  return (
    <div className={['ov-tui', className].filter(Boolean).join(' ')} {...rest}>
      <div ref={bodyRef} className="ov-tui__body">
        {records.length ? (
          records.map((record) => (
            <TuiRecordItem
              key={record.id}
              record={record}
              labels={text}
              onOpenUri={onOpenUri}
              onCopy={onCopy}
              onPickCommand={(command) => onValueChange?.(`${command} `)}
            />
          ))
        ) : (
          <div className="ov-tui__empty">
            {emptyState === undefined ? text.empty : emptyState}
          </div>
        )}
      </div>
      <TuiAssist
        value={value}
        labels={text}
        completion={completion}
        activeIndex={activeIndex}
        onActiveIndexChange={setActiveIndexSafely}
        onPick={(next) => onValueChange?.(next)}
      />
      <TuiComposer
        value={value}
        labels={text}
        completion={completion}
        disabled={disabled}
        readOnly={readOnly}
        canSubmit={Boolean(onSubmit)}
        activeIndex={activeIndex}
        onActiveIndexChange={setActiveIndexSafely}
        onValueChange={onValueChange}
        onSubmit={onSubmit}
      />
    </div>
  );
}
