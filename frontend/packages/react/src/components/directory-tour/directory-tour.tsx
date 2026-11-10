import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DirectoryTourProps, DirectoryTourStep } from './types.js';
import { useTourAnchor } from './use-tour-anchor.js';
import { useTourPosition } from './use-tour-position.js';
import { resolveLocale } from '../../shared/locale.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

/** Data-free directory guidance. Unmounting a closed session resets its steps. */
export function DirectoryTour(props: DirectoryTourProps) {
  if (!props.open || props.steps.length === 0) return null;
  return <TourSession {...props} />;
}

function TourSession(props: DirectoryTourProps) {
  const [index, setIndex] = useState(props.defaultStep ?? 0);
  const skipped = useRef(new Set<string>());
  const current = props.currentStep ?? index;
  const step = props.steps[current];
  if (!step) return null;
  const advance = (skip: boolean) => {
    if (skip) skipped.current.add(step.id);
    if (current === props.steps.length - 1) {
      props.onComplete?.({ skippedStepIds: [...skipped.current] });
    } else {
      if (props.currentStep === undefined) setIndex(current + 1);
      props.onStepChange?.(current + 1);
    }
  };
  return (
    <TourStep
      key={step.id}
      {...props}
      step={step}
      index={current}
      advance={advance}
    />
  );
}

function TourStep({
  step,
  index,
  advance,
  steps,
  locateTarget,
  locateTimeout = 10000,
  placement = 'right',
  onClose,
  labels,
  locale,
  className,
  style,
}: DirectoryTourProps & {
  step: DirectoryTourStep;
  index: number;
  advance: (skip: boolean) => void;
}) {
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  const [attempt, retry] = useState(0);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const { anchor, failed } = useTourAnchor(
    step.target,
    locateTarget,
    locateTimeout,
    attempt,
  );
  const position = useTourPosition(anchor, surface, placement);
  const titleId = useId();
  const bodyId = useId();
  // Escape is a document event, so subscribe after commit and remove the current
  // handler on callback changes/unmount instead of keeping a stale host callback.
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose?.();
      }
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [onClose]);
  // Capture browser focus before this step takes it. Restore only on unmount,
  // not when a host passes a fresh onClose callback during an unrelated render.
  useEffect(() => {
    const previous = document.activeElement;
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus({ preventScroll: true });
    };
  }, []);
  const visible = position?.visible;
  // Focus the non-modal dialog once the native top-layer surface has committed;
  // the browser owns focus, and no timers or subscriptions remain to clean up.
  useEffect(() => {
    if (surface && visible) surface.focus({ preventScroll: true });
  }, [surface, visible]);
  if (typeof document === 'undefined') return null;
  const unavailable = failed || position?.missing;
  const locating = !anchor && !failed;
  return createPortal(
    <div
      ref={setSurface}
      popover="manual"
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      aria-busy={locating}
      tabIndex={-1}
      className={['ov-directory-tour', className].filter(Boolean).join(' ')}
      style={{
        ...style,
        top: position?.top ?? 0,
        left: position?.left ?? 0,
        visibility: position?.visible ? 'visible' : 'hidden',
      }}
    >
      {anchor && !unavailable && position?.visible && (
        <div
          className="ov-directory-tour__highlight"
          aria-hidden="true"
          style={{
            top: position.targetTop,
            left: position.targetLeft,
            width: position.targetWidth,
            height: position.targetHeight,
          }}
        />
      )}
      <div className="ov-directory-tour__card">
        {onClose && (
          <button
            className="ov-directory-tour__close"
            type="button"
            aria-label={text.close}
            onClick={onClose}
          >
            ×
          </button>
        )}
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
          <span className="ov-directory-tour__progress" aria-live="polite">
            {text.progress(index + 1, steps.length)}
          </span>
          {unavailable ? (
            <>
              <button type="button" onClick={() => retry((value) => value + 1)}>
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
                (index === steps.length - 1 ? text.done : text.next)}
            </button>
          )}
        </footer>
      </div>
    </div>,
    document.body,
  );
}
