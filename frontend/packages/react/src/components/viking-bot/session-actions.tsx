import { useTrack } from '../../shared/analytics/use-track.js';
import { Button } from '../../shared/primitives/button/button.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import type { ReactNode } from 'react';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import { resolveVikingBotLabels } from './labels.js';
import type { VikingBotLabels } from './model.js';
import type { VikingBotSessions } from './capabilities.js';
import { SessionMeta } from './session-meta.js';
import { SessionHistory } from './session-history.js';

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
  const track = useTrack();
  const locale = useLocale(localeProp);
  const labels = resolveVikingBotLabels(locale, overrides);
  const select = (id: string) => {
    track('viking_bot_session_select');
    sessions?.onSelect(id);
  };
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
            <SessionHistory
              sessions={sessions}
              labels={labels}
              locale={locale}
              onSelect={select}
            />
            <Tooltip content={labels.newSession}>
              <Button
                size="small"
                type="text"
                iconOnly
                htmlType="button"
                className="ov-viking-bot__header-action"
                aria-label={labels.newSession}
                onClick={() => {
                  track('viking_bot_session_create_click');
                  sessions.onNew();
                }}
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
