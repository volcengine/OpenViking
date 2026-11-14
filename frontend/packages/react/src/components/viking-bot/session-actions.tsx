import { Button } from '../../shared/primitives/button/button.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import { useState, type ReactNode } from 'react';
import { Select, SelectOption } from '../../shared/primitives/select/index.js';
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
                <Select<string>
                  variant="simple"
                  alignment="end"
                  value={sessions.currentId ? [sessions.currentId] : []}
                  onChange={([id]) => {
                    if (id) sessions.onSelect(id);
                  }}
                  open={historyOpen}
                  onOpenChange={(open) => {
                    setHistoryOpen(open);
                    if (open) sessions.onOpenHistory?.();
                  }}
                  popupClassName="ov-viking-bot__popover ov-viking-bot__popover--bottom"
                  renderValue={() => <VikingBotIcon name="history" />}
                  emptyContent={
                    sessions.isLoading || sessions.error ? null : (
                      <Empty
                        size="compact"
                        illustration="tray"
                        title={labels.emptySessions}
                        className="ov-viking-bot__empty-history"
                      />
                    )
                  }
                  renderTrigger={({ triggerRef, triggerProps, content }) => (
                    <Button
                      {...triggerProps}
                      ref={triggerRef}
                      size="small"
                      type="text"
                      iconOnly
                      htmlType="button"
                      className="ov-viking-bot__header-action"
                      aria-label={labels.sessionHistory}
                    >
                      {content}
                    </Button>
                  )}
                  popupFooter={
                    sessions.hasMore && sessions.onLoadMore ? (
                      <Button
                        type="text"
                        size="small"
                        htmlType="button"
                        disabled={sessions.isLoading}
                        onClick={sessions.onLoadMore}
                      >
                        {labels.loadMore}
                      </Button>
                    ) : undefined
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
                    <ul role="none">
                      {sessions.items.map((item) => {
                        const date = new Date(item.updatedAt ?? '');
                        const validDate = Number.isFinite(date.getTime());
                        return (
                          <li key={item.id} role="none">
                            <SelectOption
                              value={item.id}
                              label={item.title}
                              aria-current={
                                sessions.currentId === item.id || undefined
                              }
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
                            </SelectOption>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </Select>
              </span>
            </Tooltip>
            <Tooltip content={labels.newSession}>
              <Button
                size="small"
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
