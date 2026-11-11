import { Tooltip } from '../../shared/components/tooltip/index.js';
import CliTitle from '../../../../icons/svg/tui/cli-title.svg?react';
import { ArrowRightIcon } from '../../icons/index.js';
import type { ReactNode } from 'react';
import type { TuiCommandDefinition } from './command-catalog.js';
import type { TuiQuickCommand } from './types.js';
import { DEFAULT_TUI_LABELS, type ResolvedTuiLabels } from './tui-labels.js';

/**
 * 按名称从宿主目录中取快捷命令，保持传入顺序，未命中的名称直接跳过。
 * 重复名称只保留首次出现，避免同一命令渲染成多个相同 key 的兄弟节点。
 * Resolves quick commands from the host catalogue in the given order, skipping
 * names the catalogue does not contain. Repeated names keep their first
 * occurrence so one command never renders as sibling nodes with the same key.
 */
export function pickTuiCommands(
  shortcuts: readonly TuiQuickCommand[] = [],
  commands: readonly TuiCommandDefinition[],
): readonly (TuiCommandDefinition & TuiQuickCommand)[] {
  const byName = new Map(commands.map((command) => [command.name, command]));
  const firstByName = new Map<string, TuiQuickCommand>();
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
export function TuiEmptyRegion({
  emptyState,
  commands,
  labels,
  disabled,
  onPick,
}: {
  emptyState?: ReactNode;
  commands: readonly (TuiCommandDefinition & TuiQuickCommand)[];
  labels: ResolvedTuiLabels;
  disabled?: boolean;
  onPick: (value: string) => void;
}) {
  if (emptyState !== undefined) {
    return emptyState === null ? null : (
      <div className="ov-tui__empty">{emptyState}</div>
    );
  }
  if (!commands.length) return null;
  const showTitleArtwork = labels.emptyTitle === DEFAULT_TUI_LABELS.emptyTitle;
  return (
    <div className="ov-tui__empty ov-tui__empty--hero">
      <h3
        className="ov-tui__empty-title"
        data-artwork={showTitleArtwork || undefined}
      >
        {showTitleArtwork ? (
          <>
            <span className="ov-tui__empty-title-label">
              {labels.emptyTitle}
            </span>
            <CliTitle
              className="ov-tui__empty-title-artwork"
              aria-hidden="true"
              focusable="false"
            />
          </>
        ) : (
          labels.emptyTitle
        )}
      </h3>
      <p className="ov-tui__empty-subtitle">{labels.emptyDescription}</p>
      <p className="ov-tui__empty-quick-label">{labels.quickStart}</p>
      <ul className="ov-tui__empty-list">
        {commands.map((command) => (
          <li key={command.name} className="ov-tui__empty-item">
            <Tooltip
              content={command.description}
              placement="top"
              disabled={disabled}
              className="ov-tui__quick-tooltip"
            >
              <button
                type="button"
                className="ov-tui__empty-action"
                disabled={disabled}
                onClick={() => onPick(command.value)}
              >
                <ArrowRightIcon width={14} height={14} focusable="false" />
                <span className="ov-tui__empty-content">
                  <span className="ov-tui__empty-name">{command.name}</span>
                  <span className="ov-tui__empty-desc">
                    {command.summary ?? command.description}
                  </span>
                </span>
              </button>
            </Tooltip>
          </li>
        ))}
      </ul>
    </div>
  );
}
