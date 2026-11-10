import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import type { ReactNode } from 'react';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import { Tooltip } from '../../shared/components/tooltip/index.js';
import { resolveVikingBotLabels } from './labels.js';
import type { VikingBotLabels } from './model.js';
import type { VikingBotSessions } from './capabilities.js';
import { BotPopover } from './bot-popover.js';

/** Session controls can be used alone by a host's shared header. */
export function VikingBotSessionActions({
  sessions,
  extra,
  labels: overrides,
  locale: localeProp,
}: {
  sessions?: VikingBotSessions;
  extra?: ReactNode;
  labels?: VikingBotLabels;
  locale?: Locale;
}) {
  const locale = useLocale(localeProp);
  const labels = resolveVikingBotLabels(locale, overrides);
  return (
    <LocaleProvider locale={locale}>
      <div className="ov-viking-bot__session-actions">
        {extra}
        {sessions ? (
          <>
            <BotPopover
              label={labels.sessionHistory}
              onOpen={sessions.onOpenHistory}
              trigger={(props) => (
                <Tooltip
                  content={labels.sessionHistory}
                  disabled={props['aria-expanded'] === true}
                >
                  <button
                    {...props}
                    type="button"
                    className="ov-viking-bot__header-action"
                    aria-label={labels.sessionHistory}
                  >
                    <VikingBotIcon name="history" />
                  </button>
                </Tooltip>
              )}
            >
              {(close) => (
                <div
                  className="ov-viking-bot__session-history"
                  aria-busy={sessions.isLoading}
                >
                  {sessions.isLoading ? (
                    <p role="status">{labels.loading}</p>
                  ) : null}
                  {sessions.error ? <p role="alert">{sessions.error}</p> : null}
                  {!sessions.isLoading &&
                  !sessions.error &&
                  !sessions.items.length ? (
                    <p className="ov-viking-bot__empty-history">
                      {labels.emptySessions}
                    </p>
                  ) : null}
                  <ul>
                    {sessions.items.map((item) => {
                      const date = new Date(item.updatedAt ?? '');
                      const validDate = Number.isFinite(date.getTime());
                      return (
                        <li key={item.id}>
                          <button
                            type="button"
                            aria-current={
                              sessions.currentId === item.id || undefined
                            }
                            onClick={() => {
                              close();
                              sessions.onSelect(item.id);
                            }}
                          >
                            <span>{item.title}</span>
                            {validDate ? (
                              <time dateTime={item.updatedAt}>
                                {new Intl.DateTimeFormat(locale, {
                                  dateStyle: 'short',
                                  timeStyle: 'short',
                                }).format(date)}
                              </time>
                            ) : null}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  {sessions.hasMore && sessions.onLoadMore ? (
                    <button
                      type="button"
                      disabled={sessions.isLoading}
                      onClick={sessions.onLoadMore}
                    >
                      {labels.loadMore}
                    </button>
                  ) : null}
                </div>
              )}
            </BotPopover>
            <Tooltip content={labels.newSession}>
              <button
                type="button"
                className="ov-viking-bot__header-action"
                aria-label={labels.newSession}
                onClick={sessions.onNew}
              >
                <VikingBotIcon name="newSession" />
              </button>
            </Tooltip>
          </>
        ) : null}
      </div>
    </LocaleProvider>
  );
}
