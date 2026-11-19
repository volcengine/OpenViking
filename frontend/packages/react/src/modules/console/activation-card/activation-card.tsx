import { useId, type MouseEventHandler, type ReactNode } from 'react';
import {
  ArrowRightIcon,
  DocumentIcon,
  WikiBookIcon,
} from '../../../icons/index.js';
import { useLocale } from '../../../shared/locale-context.js';
import { resolveLocale, type Locale } from '../../../shared/locale.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { LoadingIcon } from '../../../shared/primitives/loading-icon/loading-icon.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';
import type { BotActivationState } from '../use-bot-activation.js';
import { useActivationAnnouncement } from './use-activation-announcement.js';

interface ConsoleActivationCardProps {
  status: 'checking' | 'cancelled' | 'inactive' | 'activating' | 'error';
  locale?: Locale;
  onAction?: MouseEventHandler<HTMLButtonElement>;
  media?: ReactNode;
  intro?: ReactNode;
  title?: string;
  description?: string;
}

export function ConsoleActivationStatus({
  status,
  active,
  identity,
  locale: localeProp,
}: {
  status: BotActivationState['status'];
  active: boolean;
  identity: object;
  locale?: Locale;
}) {
  const locale = useLocale(localeProp);
  const labels = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
  const messages: Partial<Record<BotActivationState['status'], string>> = {
    checking: labels.checking,
    starting: labels.activating,
    cancelled: labels.cancelled,
    ready: labels.ready,
    'not-started': labels.inactive,
  };
  const message = messages[status] ?? '';
  const announce = useActivationAnnouncement(active, message, identity);
  return (
    <span
      ref={announce}
      className="ov-console-activation__announcement"
      role="status"
      aria-atomic="true"
    />
  );
}

function ActivationArtwork() {
  return (
    <div className="ov-console-activation__artwork" aria-hidden="true">
      <div className="ov-console-activation__window">
        <div className="ov-console-activation__window-bar">
          <span className="ov-console-activation__window-dots" />
          <span className="ov-console-activation__window-address" />
        </div>
        <div className="ov-console-activation__window-body">
          <div className="ov-console-activation__window-nav">
            <span />
            <span />
            <span />
            <span />
          </div>
          <div className="ov-console-activation__window-source">
            <DocumentIcon width={24} height={24} />
            <span />
            <span />
          </div>
          <div className="ov-console-activation__window-wiki">
            <WikiBookIcon width={18} height={18} />
            <span />
            <span />
            <span />
          </div>
        </div>
      </div>
    </div>
  );
}

export function ConsoleActivationCard({
  status,
  locale: localeProp,
  onAction,
  media,
  intro,
  title,
  description,
}: ConsoleActivationCardProps) {
  const locale = useLocale(localeProp);
  const labels = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
  const titleId = useId();
  const defaultIntro = intro === undefined;
  const heading = title ?? labels.title;
  const detail = description ?? labels.description;

  if (status === 'checking' || status === 'cancelled')
    return (
      <div className="ov-console-activation" aria-busy={status === 'checking'}>
        <p className="ov-console-activation__checking">
          {status === 'checking' ? <LoadingIcon /> : null}
          {status === 'checking' ? labels.checking : labels.cancelled}
        </p>
      </div>
    );

  const activating = status === 'activating';
  const failed = status === 'error';
  return (
    <section
      className="ov-console-activation"
      aria-labelledby={defaultIntro && heading ? titleId : undefined}
    >
      <div
        className={[
          'ov-console-activation__main',
          !defaultIntro &&
            intro !== null &&
            'ov-console-activation__main--custom-intro',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {defaultIntro ? (
          <>
            <div className="ov-console-activation__cover">
              {media ?? <ActivationArtwork />}
            </div>
            {heading || detail ? (
              <div className="ov-console-activation__copy">
                {heading ? (
                  <h2 className="ov-console-activation__title" id={titleId}>
                    {heading}
                  </h2>
                ) : null}
                {detail ? (
                  <p className="ov-console-activation__description">{detail}</p>
                ) : null}
              </div>
            ) : null}
          </>
        ) : intro !== null ? (
          <div className="ov-console-activation__intro">{intro}</div>
        ) : null}
        <div className="ov-console-activation__actions">
          <Button
            type="primary"
            className="ov-console-activation__action"
            disabled={!onAction && !activating}
            loading={activating}
            onClick={onAction}
          >
            {activating
              ? labels.activating
              : failed
                ? labels.retry
                : labels.enable}
            {status === 'inactive' ? <ArrowRightIcon /> : null}
          </Button>
        </div>
      </div>
    </section>
  );
}
