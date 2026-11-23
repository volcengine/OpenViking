import PencilIcon from '../../../../icons/svg/file-preview/edit.svg?react';
import DeleteIcon from '../../../../icons/svg/file-preview/delete.svg?react';
import MoreIcon from '../../../../icons/svg/file-preview/more.svg?react';
import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
import { SelectOption } from '../../shared/primitives/select/index.js';
import type { Locale } from '../../shared/locale.js';
import type { VikingBotSession, VikingBotSessions } from './capabilities.js';
import type { VikingBotLabels } from './model.js';

export type SessionHistoryAction = {
  kind: 'rename' | 'remove';
  session: VikingBotSession;
};

export function renderSessionHistoryRows({
  sessions,
  labels,
  locale,
  managed,
  onSelect,
  onAction,
}: {
  sessions: VikingBotSessions;
  labels: Required<VikingBotLabels>;
  locale: Locale;
  managed: boolean;
  onSelect: (id: string) => void;
  onAction: (action: SessionHistoryAction) => void;
}) {
  const dateFormat = new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
  const numericDateFormat = new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
  const formatTime = (date: Date) => {
    if (locale !== 'zh-CN') return dateFormat.format(date);
    const parts = Object.fromEntries(
      numericDateFormat
        .formatToParts(date)
        .map(({ type, value }) => [type, value]),
    );
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  };
  return (
    <ul role="none">
      {sessions.items.map((session) => {
        const canRename = sessions.onRename && session.canRename !== false;
        const date = new Date(session.updatedAt ?? '');
        const current = sessions.currentId === session.id || undefined;
        const content = (
          <>
            <span title={session.title}>{session.title}</span>
            {Number.isFinite(date.getTime()) ? (
              <time dateTime={session.updatedAt}>{formatTime(date)}</time>
            ) : null}
          </>
        );
        return (
          <li key={session.id} role="none">
            {managed ? (
              <>
                <DropdownItem
                  className="ov-viking-bot__history-option"
                  aria-current={current}
                  onClick={() => onSelect(session.id)}
                >
                  {content}
                </DropdownItem>
                {canRename || sessions.onRemove ? (
                  <Dropdown
                    className="ov-viking-bot__history-action-menu"
                    trigger={
                      <DropdownItem
                        closeOnClick={false}
                        className="ov-viking-bot__history-action"
                        aria-label={`${labels.sessionActions} · ${session.title}`}
                      >
                        <MoreIcon aria-hidden="true" />
                      </DropdownItem>
                    }
                  >
                    {canRename ? (
                      <DropdownItem
                        onClick={() => onAction({ kind: 'rename', session })}
                      >
                        <PencilIcon width={14} height={14} aria-hidden="true" />
                        {labels.renameSessionAction}
                      </DropdownItem>
                    ) : null}
                    {sessions.onRemove ? (
                      <DropdownItem
                        className="ov-viking-bot__history-delete"
                        onClick={() => onAction({ kind: 'remove', session })}
                      >
                        <DeleteIcon width={14} height={14} aria-hidden="true" />
                        {labels.removeSessionAction}
                      </DropdownItem>
                    ) : null}
                  </Dropdown>
                ) : null}
              </>
            ) : (
              <SelectOption
                className="ov-viking-bot__history-option"
                value={session.id}
                label={session.title}
                onClick={() => {
                  if (current) onSelect(session.id);
                }}
                aria-current={current}
              >
                {content}
              </SelectOption>
            )}
          </li>
        );
      })}
    </ul>
  );
}
