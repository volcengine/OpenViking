import { useEffect, useRef } from 'react';
import { LoadingIcon } from '../../shared/primitives/loading-icon/loading-icon.js';
import { renderSessionHistoryRows } from './session-history-rows.js';

export function SessionHistoryList(
  props: Parameters<typeof renderSessionHistoryRows>[0],
) {
  const { sessions, labels } = props;
  const historyList = useRef<HTMLDivElement>(null);
  const visibleCurrentId = sessions.items.find(
    (session) => session.id === sessions.currentId,
  )?.id;
  // Effect necessity: center the current row using committed browser geometry.
  // It may arrive on a later page, after the opening event. Keep an existing
  // keyboard focus target in view by leaving its scroll position alone.
  // A synchronous local scroll leaves no listeners or timers to clean up.
  useEffect(() => {
    const list = historyList.current;
    if (!visibleCurrentId || !list) return;
    if (list.contains(list.ownerDocument.activeElement)) return;
    const row = list.querySelector<HTMLElement>('[aria-current="true"]');
    if (!row) return;
    list.scrollTop = row.offsetTop - (list.clientHeight - row.offsetHeight) / 2;
  }, [visibleCurrentId]);
  const hasSessions = sessions.items.length > 0;
  return (
    <div
      ref={historyList}
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
      {renderSessionHistoryRows(props)}
    </div>
  );
}
