import {
  ExperienceBoardIcon,
  WikiBoardIcon,
  WikiConflictBoardIcon,
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '@openviking/react/icons';
import type { Indicator as IndicatorValue } from '#file-tree-basic-data';
const INDICATOR = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 0,
  border: 0,
  background: 'none',
  cursor: 'pointer',
  flex: 'none',
  width: 24,
  height: 24,
  marginLeft: 'auto',
  borderRadius: 4,
} as const;

export function Indicator({
  indicator,
  text,
}: {
  indicator: IndicatorValue;
  text: { experience: string; conflict: string; wiki: string };
}) {
  const pending = indicator.kind === 'wiki' && indicator.pendingConflicts;
  const label =
    indicator.kind === 'experience'
      ? text.experience
      : pending
        ? text.conflict
        : text.wiki;
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={(event) => event.stopPropagation()}
      style={{ ...INDICATOR, color: pending ? '#eb7d00' : '#0b9e6b' }}
    >
      {indicator.kind === 'experience' ? (
        <ExperienceBoardIcon width={12} height={12} aria-hidden />
      ) : pending ? (
        <WikiConflictBoardIcon width={12} height={12} aria-hidden />
      ) : (
        <WikiBoardIcon width={12} height={12} aria-hidden />
      )}
    </button>
  );
}

/** Renders the file icon the icon set maps to this name, in its brand colour. */
export function FileTypeIcon({ name }: { name: string }) {
  const Icon = getFileTypeIcon(name);
  return (
    <Icon
      aria-hidden="true"
      focusable="false"
      width={16}
      height={16}
      style={{ color: getFileTypeColor(resolveFileTypeIconKey(name)) }}
    />
  );
}
