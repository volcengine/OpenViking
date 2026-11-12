import { Button } from '../../shared/components/button/button.js';
import type { DirectoryTourProps, DirectoryTourStep } from './types.js';
import { resolveLocale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';
import { tourIllustrations } from './illustrations.js';

type Props = Pick<
  DirectoryTourProps,
  'locale' | 'labels' | 'onClose' | 'renderContent'
> & {
  step: DirectoryTourStep;
  index: number;
  total: number;
  titleId: string;
  bodyId: string;
  advance: () => void;
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
  advance,
}: Props) {
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  const illustration =
    step.illustration === undefined
      ? tourIllustrations.get(step.target)
      : step.illustration;
  return (
    <div
      className={
        renderContent
          ? 'ov-directory-tour__card ov-directory-tour__card--custom'
          : 'ov-directory-tour__card'
      }
    >
      {!renderContent && onClose && (
        <Button
          size="mini"
          type="text"
          iconOnly
          className="ov-directory-tour__close"
          htmlType="button"
          aria-label={text.close}
          onClick={onClose}
        >
          ×
        </Button>
      )}
      {renderContent ? (
        <>
          <h2 id={titleId} className="ov-directory-tour__sr-only">
            {step.title}
          </h2>
          <div id={bodyId}>
            {renderContent({
              step,
              index,
              total,
              complete: advance,
              close: () => onClose?.(),
            })}
          </div>
        </>
      ) : (
        <>
          {illustration && (
            <div
              className="ov-directory-tour__illustration"
              data-target={step.target}
            >
              {illustration}
            </div>
          )}
          <h2 id={titleId} className="ov-directory-tour__title">
            {step.title}
          </h2>
          <div id={bodyId} className="ov-directory-tour__description">
            {step.description}
          </div>
          <footer className="ov-directory-tour__actions">
            {total > 1 && (
              <span className="ov-directory-tour__progress" aria-live="polite">
                {text.progress(index + 1, total)}
              </span>
            )}
            <Button
              size="small"
              type="primary"
              htmlType="button"
              className="ov-directory-tour__primary"
              onClick={advance}
            >
              {step.nextLabel ?? (index === total - 1 ? text.done : text.next)}
            </Button>
          </footer>
        </>
      )}
    </div>
  );
}
