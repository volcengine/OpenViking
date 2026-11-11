import { Button } from '../../shared/components/button/button.js';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DirectoryTourProps, DirectoryTourStep } from './types.js';
import { useTourAnchor } from './use-tour-anchor.js';
import { useTourPosition } from './use-tour-position.js';
import { resolveLocale } from '../../shared/locale.js';
import { useLocale } from '../../shared/locale-context.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';
import { tourIllustrations } from './illustrations.js';

/** Data-free directory guidance. Unmounting a closed session resets its steps. */
export function DirectoryTour(props: DirectoryTourProps) {
  if (!props.open || props.steps.length === 0) return null;
  return <TourSession {...props} />;
}

function TourSession(props: DirectoryTourProps) {
  const { steps, currentStep, onComplete, onStepChange } = props;
  const [index, setIndex] = useState(props.defaultStep ?? 0);
  const [completed, setCompleted] = useState(false);
  const skipped = useRef(new Set<string>());
  const current = currentStep ?? index;
  const step = steps[current];
  const advance = useCallback(
    (skip: boolean) => {
      if (!step || completed) return;
      if (skip) skipped.current.add(step.id);
      if (current === steps.length - 1) {
        setCompleted(true);
        onComplete?.({ skippedStepIds: [...skipped.current] });
      } else {
        if (currentStep === undefined) setIndex(current + 1);
        onStepChange?.(current + 1);
      }
    },
    [
      step,
      completed,
      current,
      steps.length,
      currentStep,
      onComplete,
      onStepChange,
    ],
  );
  if (!step || completed) return null;
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
  locale: localeProp,
  className,
  style,
}: DirectoryTourProps & {
  step: DirectoryTourStep;
  index: number;
  advance: (skip: boolean) => void;
}) {
  const locale = useLocale(localeProp);
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  const advanced = useRef(false);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const { anchor, failed } = useTourAnchor(
    step.target,
    locateTarget,
    locateTimeout,
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
  const unavailable = failed || Boolean(position?.missing);
  const visible = Boolean(anchor && !unavailable && position?.visible);
  // DOM lookup/removal observers report external anchor availability after commit.
  // Notify the host once per keyed step; unmount discards this guard.
  useEffect(() => {
    if (unavailable && !advanced.current) {
      advanced.current = true;
      advance(true);
    }
  }, [unavailable, advance]);
  // Focus the non-modal dialog once the native top-layer surface has committed;
  // the browser owns focus, and no timers or subscriptions remain to clean up.
  useEffect(() => {
    if (surface && visible) surface.focus({ preventScroll: true });
  }, [surface, visible]);
  if (typeof document === 'undefined') return null;
  const locating = !anchor && !failed;
  const illustration =
    step.illustration === undefined
      ? tourIllustrations.get(step.target)
      : step.illustration;
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
        visibility: visible ? 'visible' : 'hidden',
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
          <span className="ov-directory-tour__progress" aria-live="polite">
            {text.progress(index + 1, steps.length)}
          </span>
          <Button
            size="small"
            type="primary"
            htmlType="button"
            className="ov-directory-tour__primary"
            disabled={locating}
            onClick={() => advance(false)}
          >
            {step.nextLabel ??
              (index === steps.length - 1 ? text.done : text.next)}
          </Button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
