import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { TuiAssist } from './tui-assist.js';
import { TuiComposer } from './tui-composer.js';
import { pickTuiCommands, TuiEmptyRegion } from './tui-empty.js';
import { resolveTuiLabels } from './tui-labels.js';
import { TuiRecordItem } from './tui-record.js';
import { useTuiCompletion } from './use-tui-completion.js';
import type { TuiProps } from './types.js';
import { getTuiCommandQuery } from './command-completion.js';
import { formatTuiCommandInput } from './command-input.js';
import { findTuiCommand } from './command-definitions.js';

export type {
  TuiCommandDefinition,
  TuiCommandExample,
  TuiCommandGroup,
  TuiCommandParam,
  TuiCommandOption,
  TuiSkeletonKind,
} from './command-catalog.js';

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
 * 命令记录、结果与输入区。宿主负责执行与会话，组件渲染传入的记录，
 * 上报用户的提交意图与已完成的复制。
 *
 * Command records, results and input. The host owns execution and sessions; the
 * component renders supplied records and reports submission intent and successful copies.
 */
export function Tui({
  records,
  value,
  disabled = false,
  emptyState,
  quickCommands,
  locale = 'en-US',
  commands = [],
  commandGroups = [],
  onValueChange,
  onSubmit,
  onOpenUri,
  onCopy,
  labels,
  className,
  ...rest
}: TuiProps) {
  const text = resolveTuiLabels(labels, locale);
  const bodyRef = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);
  const composerRef = useRef<HTMLDivElement>(null);
  const assistId = useId();
  const [dismissedValue, setDismissedValue] = useState<string | null>(null);
  const [paletteScope, setPaletteScope] = useState<string | null>(null);
  const [selection, setSelection] = useState({ queryKey: value, index: -1 });
  const dismissCompletion = useCallback((next: string) => {
    setDismissedValue(next);
    setPaletteScope(null);
    setSelection({ queryKey: next, index: -1 });
  }, []);
  const updateValue = (next: string) => {
    setDismissedValue(null);
    setSelection({ queryKey: next, index: -1 });
    if (getTuiCommandQuery(next, commands).scope !== paletteScope)
      setPaletteScope(null);
    onValueChange?.(next);
  };
  const readOnly = !onValueChange;
  const fillCommand = (next: string) => {
    if (disabled || readOnly) return;
    updateValue(formatTuiCommandInput(next, commands));
    composerRef.current
      ?.querySelector('textarea')
      ?.focus({ preventScroll: true });
  };
  const completion = useTuiCompletion(
    value,
    !disabled && !readOnly && dismissedValue !== value,
    commands,
    commandGroups,
    paletteScope,
  );
  const queryKey = value;
  const activeIndex =
    selection.queryKey === queryKey &&
    selection.index < completion.options.length
      ? selection.index
      : -1;
  const setActiveIndexSafely = useCallback(
    (next: number | ((index: number) => number)) => {
      setSelection((current) => {
        const index =
          current.queryKey === queryKey &&
          current.index < completion.options.length
            ? current.index
            : -1;
        return {
          queryKey,
          index: typeof next === 'function' ? next(index) : next,
        };
      });
    },
    [queryKey, completion.options.length],
  );

  // useEffect 必要性：记录提交到浏览器 DOM 后滚动实际容器，渲染时无法获取最终高度。
  // 仅在用户接近底部或主动提交时跟随；同步写入，不创建订阅或待清理资源。
  useEffect(() => {
    const body = bodyRef.current;
    if (body && followOutput.current) body.scrollTop = body.scrollHeight;
  }, [records]);

  return (
    <div className={['ov-tui', className].filter(Boolean).join(' ')} {...rest}>
      <div
        ref={bodyRef}
        className="ov-tui__body"
        onScroll={(event) => {
          const body = event.currentTarget;
          followOutput.current =
            body.scrollHeight - body.scrollTop - body.clientHeight <= 24;
        }}
      >
        {records.length ? (
          records.map((record) => (
            <TuiRecordItem
              key={record.id}
              record={record}
              command={findTuiCommand(record.command, commands)}
              labels={text}
              onOpenUri={onOpenUri}
              onCopy={onCopy}
              onPickCommand={
                disabled || readOnly
                  ? undefined
                  : (command) => fillCommand(`${command} `)
              }
            />
          ))
        ) : (
          <TuiEmptyRegion
            emptyState={emptyState}
            commands={pickTuiCommands(quickCommands, commands)}
            labels={text}
            disabled={disabled || readOnly}
            onPick={(command) => fillCommand(`${command.name} `)}
          />
        )}
      </div>
      <TuiAssist
        assistId={assistId}
        anchorRef={composerRef}
        onDismiss={dismissCompletion}
        labels={text}
        completion={completion}
        activeIndex={activeIndex}
        onActiveIndexChange={setActiveIndexSafely}
        onPick={fillCommand}
      />
      <TuiComposer
        onDismiss={dismissCompletion}
        onRequestPalette={() => {
          setDismissedValue(null);
          setSelection({ queryKey: value, index: -1 });
          setPaletteScope(completion.query.scope);
        }}
        assistId={assistId}
        anchorRef={composerRef}
        onFocus={() => setDismissedValue(null)}
        value={value}
        history={records.map((record) =>
          formatTuiCommandInput(record.command, commands),
        )}
        labels={text}
        completion={completion}
        disabled={disabled}
        readOnly={readOnly}
        canSubmit={Boolean(onSubmit) && !readOnly}
        activeIndex={activeIndex}
        onActiveIndexChange={setActiveIndexSafely}
        onValueChange={onValueChange ? updateValue : undefined}
        onSubmit={(command) => {
          if (disabled || readOnly) return;
          followOutput.current = true;
          onSubmit?.(command);
        }}
        onOpenUri={onOpenUri}
      />
    </div>
  );
}
