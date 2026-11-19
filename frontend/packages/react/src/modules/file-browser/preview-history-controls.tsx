import { Switch } from '../../shared/primitives/switch/switch.js';
import CopyIcon from '../../../../icons/svg/file-preview/copy.svg?react';
import { Button } from '../../shared/primitives/button/button.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import { Select, SelectOption } from '../../shared/primitives/select/index.js';
import type { usePreviewHistory } from './use-preview-history.js';
import type { PreviewHistoryProps } from './preview-history.js';
import type {
  FileBrowserWorkflowLabels,
  PreviewHistoryEntry,
} from './preview-workflow-types.js';
export function historyVersionLabel(
  entry: PreviewHistoryEntry,
  locale: PreviewHistoryProps['locale'],
) {
  if (!entry.timestamp) return entry.title;
  const parts = new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(entry.timestamp * 1000));
  const part = (type: string) =>
    parts.find((item) => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`;
}

export function historyVersionBadge(
  entry: PreviewHistoryEntry,
  deleted: boolean,
  text: FileBrowserWorkflowLabels,
) {
  if (deleted) return text.deletedBadge;
  return entry.restored ? text.rollbackBadge : undefined;
}

export function PreviewHistoryControls({
  history,
  text,
  locale,
  comparison = false,
}: {
  history: ReturnType<typeof usePreviewHistory>;
  text: PreviewHistoryProps['text'];
  locale: PreviewHistoryProps['locale'];
  comparison?: boolean;
}) {
  const entries = history.query.data ?? [];
  return (
    <div
      className={
        comparison
          ? 'ov-file-history__comparison-control'
          : 'ov-file-history__directory-version-controls'
      }
    >
      {comparison ? <span>{text.comparisonVersion}</span> : null}
      <Select<string>
        aria-label={comparison ? text.comparisonVersion : text.selectVersion}
        value={
          (comparison ? history.base?.id : history.selected?.id)
            ? [(comparison ? history.base?.id : history.selected?.id)!]
            : []
        }
        locale={locale}
        disabled={history.query.loading || !entries.length}
        onChange={(values) =>
          comparison ? history.setBaseId(values[0]) : history.select(values[0]!)
        }
      >
        {entries
          .filter((entry) => !comparison || entry.id !== history.selected?.id)
          .map((entry) => {
            const badge = historyVersionBadge(
              entry,
              history.deletedIds.has(entry.id),
              text,
            );
            return (
              <SelectOption key={entry.id} value={entry.id}>
                {historyVersionLabel(entry, locale)}
                {badge ? (
                  <span className="ov-file-history__version-badge">
                    {badge}
                  </span>
                ) : null}
              </SelectOption>
            );
          })}
      </Select>
      {!comparison ? (
        <>
          <span>{text.compareChanges}</span>
          <Switch
            size="small"
            aria-label={text.compareChanges}
            checked={history.compare}
            disabled={!history.canCompare}
            onChange={history.setCompare}
          />
        </>
      ) : null}
    </div>
  );
}

export function PreviewHistoryPath({
  path,
  text,
  onCopy,
}: {
  path: string;
  text: PreviewHistoryProps['text'];
  onCopy: () => void;
}) {
  return (
    <div className="ov-file-history__file-path">
      <Tooltip content={path}>
        <span>{path}</span>
      </Tooltip>
      <Tooltip content={text.copyPath} focusVisibleOnly>
        <Button
          size="small"
          type="text"
          aria-label={text.copyPath}
          icon={<CopyIcon aria-hidden="true" />}
          onClick={onCopy}
        />
      </Tooltip>
    </div>
  );
}
