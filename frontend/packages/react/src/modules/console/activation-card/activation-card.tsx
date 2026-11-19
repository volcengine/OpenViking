import { useId, type ReactNode } from 'react';
import { ArrowRightIcon } from '../../../icons/index.js';
import { useLocale } from '../../../shared/locale-context.js';
import { resolveLocale, type Locale } from '../../../shared/locale.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { LoadingIcon } from '../../../shared/primitives/loading-icon/loading-icon.js';
import { ActivationIntroMedia } from './intro-media.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

interface ConsoleActivationCardProps {
  status: 'checking' | 'inactive' | 'activating' | 'error';
  locale?: Locale;
  onAction?: () => void;
  media?: ReactNode;
  error?: ReactNode;
}

export function ConsoleActivationCard({
  status,
  locale: localeProp,
  onAction,
  media,
  error,
}: ConsoleActivationCardProps) {
  const locale = useLocale(localeProp);
  const labels = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
  const titleId = useId();

  if (status === 'checking')
    return (
      <div className="ov-console-activation">
        <p className="ov-console-activation__checking" role="status">
          <LoadingIcon />
          {labels.checking}
        </p>
      </div>
    );

  const activating = status === 'activating';
  const failed = status === 'error';
  return (
    <section className="ov-console-activation" aria-labelledby={titleId}>
      <div className="ov-console-activation__main">
        <div className="ov-console-activation__cover">
          {media ?? <ActivationIntroMedia locale={locale} />}
        </div>
        <div className="ov-console-activation__copy">
          <h2 className="ov-console-activation__title" id={titleId}>
            {labels.title}
          </h2>
          <p className="ov-console-activation__description">
            {labels.description}
          </p>
        </div>
        <div className="ov-console-activation__actions">
          {failed ? (
            <div className="ov-console-activation__error" role="alert">
              {error ?? labels.failed}
            </div>
          ) : null}
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
          <span className="ov-console-activation__announcement" role="status">
            {activating ? labels.activating : ''}
          </span>
        </div>
      </div>
    </section>
  );
}
