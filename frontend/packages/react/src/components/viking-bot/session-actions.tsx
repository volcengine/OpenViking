import { Button } from '../../shared/primitives/button/button.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import { useState, type ReactNode } from 'react';
import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import { resolveVikingBotLabels } from './labels.js';
import type { VikingBotLabels } from './model.js';
import type { VikingBotSessions } from './capabilities.js';
import { SessionMeta } from './session-meta.js';

/** Session controls can be used alone by a host's shared header. */
export function VikingBotSessionActions({
  sessions,
  extra,
  onOpenUri,
  onCopy,
  labels: overrides,
  locale: localeProp,
}: {
  sessions?: VikingBotSessions;
  extra?: ReactNode;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  labels?: VikingBotLabels;
  locale?: Locale;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const locale = useLocale(localeProp);
  const labels = resolveVikingBotLabels(locale, overrides);
  return (
    <LocaleProvider locale={locale}>
      <div className="ov-viking-bot__session-actions">
        {sessions?.currentId ? (
          <SessionMeta
            id={sessions.currentId}
            uri={sessions.currentUri}
            labels={labels}
            onOpenUri={onOpenUri}
            onCopy={onCopy}
          />
        ) : null}
        {extra}
        {sessions ? (
          <>
            <Tooltip content={labels.sessionHistory} disabled={historyOpen}>
              <span className="ov-viking-bot__popover-anchor">
                <Dropdown
                  open={historyOpen}
                  onOpenChange={(open) => {
                    setHistoryOpen(open);
                    if (open) sessions.onOpenHistory?.();
                  }}
                  className="ov-viking-bot__popover ov-viking-bot__popover--bottom"
                  trigger={
                    <Button
                      size="mini"
                      type="text"
                      iconOnly
                      htmlType="button"
                      className="ov-viking-bot__header-action"
                      aria-label={labels.sessionHistory}
                    >
                      <VikingBotIcon name="history" />
                    </Button>
                  }
                >
                  <div
                    className="ov-viking-bot__session-history"
                    aria-busy={sessions.isLoading}
                  >
                    {sessions.isLoading ? (
                      <p role="status">{labels.loading}</p>
                    ) : null}
                    {sessions.error ? (
                      <p role="alert">{sessions.error}</p>
                    ) : null}
                    {!sessions.isLoading &&
                    !sessions.error &&
                    !sessions.items.length ? (
                      <p className="ov-viking-bot__empty-history">
                        {labels.emptySessions}
                      </p>
                    ) : null}
                    <ul role="none">
                      {sessions.items.map((item) => {
                        const date = new Date(item.updatedAt ?? '');
                        const validDate = Number.isFinite(date.getTime());
                        return (
                          <li key={item.id} role="none">
                            <DropdownItem
                              aria-current={
                                sessions.currentId === item.id || undefined
                              }
                              onClick={() => {
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
                            </DropdownItem>
                          </li>
                        );
                      })}
                    </ul>
                    {sessions.hasMore && sessions.onLoadMore ? (
                      <DropdownItem
                        closeOnClick={false}
                        disabled={sessions.isLoading}
                        onClick={sessions.onLoadMore}
                      >
                        {labels.loadMore}
                      </DropdownItem>
                    ) : null}
                  </div>
                </Dropdown>
              </span>
            </Tooltip>
            <Tooltip content={labels.newSession}>
              <Button
                size="mini"
                type="text"
                iconOnly
                htmlType="button"
                className="ov-viking-bot__header-action"
                aria-label={labels.newSession}
                onClick={sessions.onNew}
              >
                <VikingBotIcon name="newSession" />
              </Button>
            </Tooltip>
          </>
        ) : null}
      </div>
    </LocaleProvider>
  );
}
