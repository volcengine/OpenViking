import { useRef, useState } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
import { LoadingIcon } from '../../shared/primitives/loading-icon/loading-icon.js';
import { Select } from '../../shared/primitives/select/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { Locale } from '../../shared/locale.js';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import type { VikingBotSessions } from './capabilities.js';
import type { VikingBotLabels } from './model.js';
import { SessionHistoryDialog } from './session-history-dialog.js';

import {
  renderSessionHistoryRows,
  type SessionHistoryAction,
} from './session-history-rows.js';

const popupClass = 'ov-viking-bot__popover ov-viking-bot__history-popover';

type PendingAction = SessionHistoryAction & {
  scopeKey: VikingBotSessions['scopeKey'];
  callback: VikingBotSessions['onRename'] | VikingBotSessions['onRemove'];
};

export function SessionHistory({
  sessions,
  labels,
  locale,
  onSelect,
}: {
  sessions: VikingBotSessions;
  labels: Required<VikingBotLabels>;
  locale: Locale;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<PendingAction>();
  const trigger = useRef<HTMLButtonElement>(null);
  const { scopeKey, onRename, onRemove } = sessions;
  const actionCallback = (kind: SessionHistoryAction['kind']) =>
    kind === 'rename' ? onRename : onRemove;
  // Discard the pending action before rendering its dialog for a new owner.
  if (action) {
    const callback = actionCallback(action.kind);
    if (
      action.scopeKey !== scopeKey ||
      !callback ||
      (scopeKey === undefined && action.callback !== callback)
    ) {
      setAction(undefined);
    }
  }
  const managed = Boolean(sessions.onRename || sessions.onRemove);
  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (next) sessions.onOpenHistory?.();
  };
  const historyTrigger = (
    <Button
      ref={trigger}
      size="small"
      type="text"
      iconOnly
      className="ov-viking-bot__header-action"
      aria-label={labels.sessionHistory}
    >
      <VikingBotIcon name="history" />
    </Button>
  );
  const hasSessions = sessions.items.length > 0;
  const hasMore = sessions.hasMore && sessions.onLoadMore;
  const list = (
    <div
      className="ov-viking-bot__session-history"
      aria-busy={sessions.isLoading}
    >
      {!hasSessions && !sessions.isLoading && !sessions.error ? (
        <div role="status" className="ov-viking-bot__history-placeholder">
          {labels.emptySessions}
        </div>
      ) : null}
      {sessions.isLoading ? (
        <div
          role="status"
          className={
            hasSessions
              ? 'ov-viking-bot__history-refreshing'
              : 'ov-viking-bot__history-placeholder'
          }
        >
          {hasSessions ? null : <LoadingIcon />}
          {labels.loading}
        </div>
      ) : null}
      {sessions.error ? (
        <p
          role="alert"
          className={
            hasSessions
              ? 'ov-viking-bot__history-error'
              : 'ov-viking-bot__history-placeholder'
          }
        >
          {sessions.error}
        </p>
      ) : null}
      {renderSessionHistoryRows({
        sessions,
        labels,
        locale,
        managed,
        onSelect,
        onAction: (next) => {
          setOpen(false);
          setAction({ ...next, scopeKey, callback: actionCallback(next.kind) });
        },
      })}
    </div>
  );
  return (
    <>
      <Tooltip content={labels.sessionHistory} disabled={open}>
        <span className="ov-viking-bot__popover-anchor">
          {managed ? (
            <Dropdown
              open={open}
              onOpenChange={changeOpen}
              className={popupClass}
              menuClassName="ov-viking-bot__managed-session-history"
              trigger={historyTrigger}
            >
              {list}
              {hasMore ? (
                <DropdownItem
                  closeOnClick={false}
                  disabled={sessions.isLoading}
                  onClick={sessions.onLoadMore}
                >
                  {labels.loadMore}
                </DropdownItem>
              ) : null}
            </Dropdown>
          ) : (
            <Select<string>
              variant="simple"
              alignment="end"
              value={sessions.currentId ? [sessions.currentId] : []}
              onChange={([id]) => {
                if (id) onSelect(id);
              }}
              open={open}
              onOpenChange={changeOpen}
              popupClassName={popupClass}
              renderValue={() => <VikingBotIcon name="history" />}
              emptyContent={null}
              renderTrigger={({ triggerRef, triggerProps, content }) => (
                <Button
                  {...triggerProps}
                  ref={triggerRef}
                  size="small"
                  type="text"
                  iconOnly
                  className="ov-viking-bot__header-action"
                  aria-label={labels.sessionHistory}
                >
                  {content}
                </Button>
              )}
              popupFooter={
                hasMore ? (
                  <Button
                    type="text"
                    size="small"
                    disabled={sessions.isLoading}
                    onClick={sessions.onLoadMore}
                  >
                    {labels.loadMore}
                  </Button>
                ) : undefined
              }
            >
              {list}
            </Select>
          )}
        </span>
      </Tooltip>
      {action ? (
        <SessionHistoryDialog
          kind={action.kind}
          session={action.session}
          sessions={sessions}
          labels={labels}
          locale={locale}
          triggerRef={trigger}
          onClose={() =>
            setAction((current) => (current === action ? undefined : current))
          }
        />
      ) : null}
    </>
  );
}
