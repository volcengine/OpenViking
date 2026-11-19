import { useRef, useState } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
import { Select } from '../../shared/primitives/select/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { Locale } from '../../shared/locale.js';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import type { VikingBotSessions } from './capabilities.js';
import type { VikingBotLabels } from './model.js';
import { SessionHistoryDialog } from './session-history-dialog.js';

import { SessionHistoryList } from './session-history-list.js';
import type { SessionHistoryAction } from './session-history-rows.js';
import { useSessionHistoryPaging } from './use-session-history-paging.js';

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
  const paging = useSessionHistoryPaging(open, sessions);
  const loading = sessions.isLoading || paging.loading;
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
  const list = (
    <SessionHistoryList
      sessions={{
        ...sessions,
        isLoading: loading,
        error: sessions.error ?? paging.error,
      }}
      labels={labels}
      locale={locale}
      managed={managed}
      onSelect={onSelect}
      onAction={(next) => {
        setOpen(false);
        setAction({ ...next, scopeKey, callback: actionCallback(next.kind) });
      }}
    />
  );
  return (
    <>
      <Tooltip content={labels.sessionHistory} disabled={open} focusVisibleOnly>
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
              {paging.canLoad ? (
                <DropdownItem
                  className="ov-viking-bot__history-load-more"
                  closeOnClick={false}
                  disabled={loading}
                  onClick={paging.loadMore}
                >
                  {paging.loading ? labels.loading : labels.loadMore}
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
                paging.canLoad ? (
                  <Button
                    type="text"
                    size="small"
                    className="ov-viking-bot__history-load-more"
                    disabled={loading}
                    onClick={paging.loadMore}
                  >
                    {paging.loading ? labels.loading : labels.loadMore}
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
