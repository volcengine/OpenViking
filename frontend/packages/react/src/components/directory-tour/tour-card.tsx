import type { DirectoryTourProps, DirectoryTourStep } from './types.js';
import { resolveLocale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

type Props = Pick<
  DirectoryTourProps,
  'locale' | 'labels' | 'onClose' | 'renderContent'
> & {
  step: DirectoryTourStep;
  index: number;
  total: number;
  titleId: string;
  bodyId: string;
  locating: boolean;
  unavailable: boolean;
  retry: () => void;
  advance: (skip: boolean) => void;
};

export function TourCard({
  step,
  index,
  total,
  locale,
  labels,
  onClose,
  renderContent,
  titleId,
  bodyId,
  locating,
  unavailable,
  retry,
  advance,
}: Props) {
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  const custom = renderContent && !locating && !unavailable;
  return (
    <div
      className={
        custom
          ? 'ov-directory-tour__card ov-directory-tour__card--custom'
          : 'ov-directory-tour__card'
      }
    >
      {!custom && onClose && (
        <button
          className="ov-directory-tour__close"
          type="button"
          aria-label={text.close}
          onClick={onClose}
        >
          ×
        </button>
      )}
      {custom ? (
        <>
          <h2 id={titleId} className="ov-directory-tour__sr-only">
            {step.title}
          </h2>
          <div id={bodyId}>
            {renderContent({
              step,
              index,
              total,
              complete: () => advance(false),
              close: () => onClose?.(),
            })}
          </div>
        </>
      ) : (
        <>
          {!locating && !unavailable && step.illustration && (
            <div className="ov-directory-tour__illustration">
              {step.illustration}
            </div>
          )}
          <h2 id={titleId} className="ov-directory-tour__title">
            {step.title}
          </h2>
          <div
            id={bodyId}
            className="ov-directory-tour__description"
            role={unavailable ? 'alert' : undefined}
          >
            {unavailable
              ? text.unavailable
              : locating
                ? text.locating
                : step.description}
          </div>
          <footer className="ov-directory-tour__actions">
            {total > 1 && (
              <span className="ov-directory-tour__progress" aria-live="polite">
                {text.progress(index + 1, total)}
              </span>
            )}
            {unavailable ? (
              <>
                <button type="button" onClick={retry}>
                  {text.retry}
                </button>
                <button type="button" onClick={() => advance(true)}>
                  {text.skip}
                </button>
              </>
            ) : (
              <button
                type="button"
                className="ov-directory-tour__primary"
                disabled={locating}
                onClick={() => advance(false)}
              >
                {step.nextLabel ??
                  (index === total - 1 ? text.done : text.next)}
              </button>
            )}
          </footer>
        </>
      )}
    </div>
  );
}
