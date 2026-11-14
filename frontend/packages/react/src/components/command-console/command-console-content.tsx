import { useTrack } from '../../shared/analytics/use-track.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { CommandConsoleAssist } from './command-console-assist.js';
import { CommandConsoleComposer } from './command-console-composer.js';
import {
  pickCommandConsoleCommands,
  CommandConsoleEmptyRegion,
} from './command-console-empty.js';
import { resolveCommandConsoleLabels } from './command-console-labels.js';
import { CommandConsoleRecordItem } from './command-console-record.js';
import { useCommandConsoleCompletion } from './use-command-console-completion.js';
import type { CommandConsoleProps } from './types.js';
import { getCommandConsoleCommandQuery } from './command-completion.js';
import { formatCommandConsoleCommandInput } from './command-input.js';
import { findCommandConsoleCommand } from './command-definitions.js';

/**
 * 命令记录、结果与输入区。宿主负责执行与会话，组件渲染传入的记录，
 * 上报用户的提交意图与已完成的复制。
 *
 * Command records, results and input. The host owns execution and sessions; the
 * component renders supplied records and reports submission intent and successful copies.
 */
export function CommandConsoleContent({
  records,
  value,
  disabled = false,
  emptyState,
  quickCommands,
  locale: localeProp,
  commands = [],
  commandGroups = [],
  onValueChange,
  onSubmit,
  onOpenUri,
  onCopy,
  labels,
  className,
  ...rest
}: CommandConsoleProps) {
  const track = useTrack();
  const exposure = useExposure('console_command_panel_show');
  const locale = useLocale(localeProp);
  const text = resolveCommandConsoleLabels(labels, locale);
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
    if (getCommandConsoleCommandQuery(next, commands).scope !== paletteScope)
      setPaletteScope(null);
    onValueChange?.(next);
  };
  const readOnly = !onValueChange;
  const fillInput = (next: string) => {
    if (disabled || readOnly) return;
    updateValue(next);
    composerRef.current
      ?.querySelector('textarea')
      ?.focus({ preventScroll: true });
  };
  const fillCommand = (next: string) =>
    fillInput(formatCommandConsoleCommandInput(next, commands));
  const completion = useCommandConsoleCompletion(
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
    <LocaleProvider locale={locale}>
      <div
        className={['ov-command-console', className].filter(Boolean).join(' ')}
        {...rest}
        ref={exposure}
      >
        <div
          ref={bodyRef}
          className="ov-command-console__body"
          onScroll={(event) => {
            const body = event.currentTarget;
            followOutput.current =
              body.scrollHeight - body.scrollTop - body.clientHeight <= 24;
          }}
        >
          {records.length ? (
            records.map((record) => (
              <CommandConsoleRecordItem
                key={record.id}
                record={record}
                locale={locale}
                command={findCommandConsoleCommand(record.command, commands)}
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
            <CommandConsoleEmptyRegion
              emptyState={emptyState}
              commands={pickCommandConsoleCommands(quickCommands, commands)}
              labels={text}
              disabled={disabled || readOnly}
              onPick={(value) => {
                track('console_command_preset_select');
                fillInput(value);
              }}
            />
          )}
        </div>
        <CommandConsoleAssist
          assistId={assistId}
          anchorRef={composerRef}
          onDismiss={dismissCompletion}
          labels={text}
          completion={completion}
          activeIndex={activeIndex}
          onActiveIndexChange={setActiveIndexSafely}
          onPick={fillCommand}
        />
        <CommandConsoleComposer
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
            formatCommandConsoleCommandInput(record.command, commands),
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
            track('command_console_command_submit');
            followOutput.current = true;
            onSubmit?.(command);
          }}
          onOpenUri={onOpenUri}
        />
      </div>
    </LocaleProvider>
  );
}
