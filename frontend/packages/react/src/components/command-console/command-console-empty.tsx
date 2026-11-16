import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import CliTitle from '../../../../icons/svg/command-console/cli-title.svg?react';
import { ArrowRightIcon } from '../../icons/index.js';
import type { ReactNode } from 'react';
import type { CommandConsoleCommandDefinition } from './command-catalog.js';
import type { CommandConsoleQuickCommand } from './types.js';
import {
  DEFAULT_COMMAND_CONSOLE_LABELS,
  type ResolvedCommandConsoleLabels,
} from './command-console-labels.js';
import { useCommandConsoleQuickOverflow } from './use-command-console-quick-overflow.js';

/**
 * 按名称从宿主目录中取快捷命令，保持传入顺序，未命中的名称直接跳过。
 * 重复名称只保留首次出现，避免同一命令渲染成多个相同 key 的兄弟节点。
 * Resolves quick commands from the host catalogue in the given order, skipping
 * names the catalogue does not contain. Repeated names keep their first
 * occurrence so one command never renders as sibling nodes with the same key.
 */
export function pickCommandConsoleCommands(
  shortcuts: readonly CommandConsoleQuickCommand[] = [],
  commands: readonly CommandConsoleCommandDefinition[],
): readonly (CommandConsoleCommandDefinition & CommandConsoleQuickCommand)[] {
  const byName = new Map(commands.map((command) => [command.name, command]));
  const firstByName = new Map<string, CommandConsoleQuickCommand>();
  for (const shortcut of shortcuts) {
    if (!firstByName.has(shortcut.name))
      firstByName.set(shortcut.name, shortcut);
  }
  return [...firstByName.values()].flatMap((shortcut) => {
    const command = byName.get(shortcut.name);
    return command ? [{ ...command, value: shortcut.value }] : [];
  });
}

/**
 * 无记录时的空态区域。宿主节点优先；否则展示内置空态，没有可用快捷命令时
 * 不渲染任何内容。
 *
 * Empty region shown without records. A host node wins; otherwise the built-in
 * empty state renders, and nothing is shown when no quick command resolves.
 */
export function CommandConsoleEmptyRegion({
  emptyState,
  commands,
  labels,
  disabled,
  onPick,
}: {
  emptyState?: ReactNode;
  commands: readonly (CommandConsoleCommandDefinition &
    CommandConsoleQuickCommand)[];
  labels: ResolvedCommandConsoleLabels;
  disabled?: boolean;
  onPick: (value: string) => void;
}) {
  // Hook 必须在提前返回之前调用；内置空态不渲染时列表 ref 为空，测量直接跳过。
  // Hooks run before the early returns; with no built-in empty state the list ref
  // stays null and the measurement is skipped.
  const { listRef, truncated } = useCommandConsoleQuickOverflow();
  if (emptyState !== undefined) {
    return emptyState === null ? null : (
      <div className="ov-command-console__empty">{emptyState}</div>
    );
  }
  if (!commands.length) return null;
  const showTitleArtwork =
    labels.emptyTitle === DEFAULT_COMMAND_CONSOLE_LABELS.emptyTitle;
  return (
    <div className="ov-command-console__empty ov-command-console__empty--hero">
      <h3
        className="ov-command-console__empty-title"
        data-artwork={showTitleArtwork || undefined}
      >
        {showTitleArtwork ? (
          <>
            <span className="ov-command-console__empty-title-label">
              {labels.emptyTitle}
            </span>
            <CliTitle
              className="ov-command-console__empty-title-artwork"
              aria-hidden="true"
              focusable="false"
            />
          </>
        ) : (
          labels.emptyTitle
        )}
      </h3>
      <p className="ov-command-console__empty-subtitle">
        {labels.emptyDescription}
      </p>
      <p className="ov-command-console__empty-quick-label">
        {labels.quickStart}
      </p>
      <ul ref={listRef} className="ov-command-console__empty-list">
        {commands.map((command, index) => {
          const action = (
            <button
              type="button"
              className="ov-command-console__empty-action"
              disabled={disabled}
              onClick={() => onPick(command.value)}
            >
              <ArrowRightIcon width={14} height={14} focusable="false" />
              <span className="ov-command-console__empty-content">
                <span className="ov-command-console__empty-name">
                  {command.name}
                </span>
                <span className="ov-command-console__empty-desc">
                  {command.summary ?? command.description}
                </span>
              </span>
            </button>
          );
          // 行内显示 summary ?? description：宿主另给了 summary 时完整说明被藏起来，
          // 没有 summary 但说明被单行省略截断时同样看不到全文。两种情况下 Tooltip
          // 才有行内没有的内容，其余情况弹出的会是同一句话。
          //
          // Tooltip 始终挂载、只切 disabled，而不是按需包一层：包与不包会替换整棵
          // 子树，说明节点随之卸载，截断测量仍盯着已脱离 DOM 的旧节点，测出的 0
          // 宽又会把 Tooltip 关掉。保持节点身份稳定，订阅才一直有效。
          //
          // The row shows `summary ?? description`: the full description is hidden
          // when the host supplied a `summary`, and equally when the description is
          // truncated by the single-line ellipsis. Only then does the Tooltip add
          // something the row does not already show.
          //
          // The Tooltip stays mounted and only toggles `disabled` instead of being
          // added on demand: adding it replaces the whole subtree, so the
          // description node unmounts while the measurement still watches the
          // detached old node, whose zero width then switches the Tooltip back off.
          // Keeping the node's identity stable keeps the subscription valid.
          const hasTooltip =
            command.summary !== undefined || truncated.includes(index);
          return (
            <li key={command.name} className="ov-command-console__empty-item">
              <Tooltip
                content={command.description}
                placement="top"
                disabled={disabled || !hasTooltip}
                className="ov-command-console__quick-tooltip"
              >
                {action}
              </Tooltip>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
