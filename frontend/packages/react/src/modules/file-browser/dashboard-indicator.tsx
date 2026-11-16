import { dashboardLabels } from './dashboard-labels.js';
import type { FileBrowserDashboardLabels } from './types.js';
import {
  ExperienceBoardIcon,
  WikiBoardIcon,
  WikiConflictBoardIcon,
} from '../../icons/index.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';

export function DashboardIndicator({
  dashboard,
  locale,
  labels,
  onOpen,
}: {
  dashboard: FileBrowserDashboard;
  locale?: 'en-US' | 'zh-CN';
  labels?: FileBrowserDashboardLabels;
  onOpen: (uri: string) => void;
}) {
  const text = dashboardLabels(locale, labels);
  const label =
    dashboard.kind === 'experience'
      ? text.openExperience
      : dashboard.pendingConflicts
        ? text.wikiConflicts
        : text.openWiki;
  const Icon =
    dashboard.kind === 'experience'
      ? ExperienceBoardIcon
      : dashboard.pendingConflicts
        ? WikiConflictBoardIcon
        : WikiBoardIcon;
  return (
    <Tooltip content={label}>
      <button
        type="button"
        className={`ov-file-browser__dashboard${dashboard.pendingConflicts ? ' ov-file-browser__dashboard--pending' : ''}`}
        aria-label={label}
        onClick={(event) => {
          event.stopPropagation();
          onOpen(dashboard.uri);
        }}
      >
        <Icon width={12} height={12} />
      </button>
    </Tooltip>
  );
}
