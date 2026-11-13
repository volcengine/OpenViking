import type { CSSProperties } from 'react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DirectoryTourProps, DirectoryTourStep } from './types.js';
import { useTourAnchor } from './use-tour-anchor.js';
import { useTourPosition } from './use-tour-position.js';
import { useTourTheme } from './use-tour-theme.js';
import { TourCard } from './tour-card.js';
import { useLocale } from '../../shared/locale-context.js';

/** Data-free directory guidance. Unmounting a closed session resets its steps. */
export function DirectoryTour(props: DirectoryTourProps) {
  const [portalRoot, setPortalRoot] = useState<HTMLElement | null>(null);
  // Resolve the external portal container after hydration, so the server and
  // first client render agree. The body is document-owned and needs no cleanup.
  useEffect(() => setPortalRoot(document.body), []);
  if (!portalRoot || !props.open || props.steps.length === 0) return null;
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
  renderContent,
}: DirectoryTourProps & {
  step: DirectoryTourStep;
  index: number;
  advance: (skip: boolean) => void;
}) {
  const locale = useLocale(localeProp);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const { anchor, elements, failed } = useTourAnchor(
    step.target,
    locateTarget,
    locateTimeout,
  );
  const position = useTourPosition(anchor, surface, placement, elements);
  useTourTheme(anchor, surface);
  const titleId = useId();
  const bodyId = useId();
  // Handle Escape at the window bubble phase, after document-level overlays
  // have had a chance to claim it. Remove the listener on change or unmount.
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && onClose) {
        event.preventDefault();
        onClose?.();
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);
  // Capture browser focus before this step takes it. Restore only on unmount,
  // not when a host passes a fresh onClose callback during an unrelated render.
  useEffect(() => {
    const previous: (Element & { focus?: HTMLElement['focus'] }) | null =
      document.activeElement;
    return () => {
      if (previous?.isConnected) previous.focus?.({ preventScroll: true });
    };
  }, []);
  const unavailable = failed || Boolean(position?.missing);
  const visible = Boolean(
    position && (unavailable || (anchor && position.visible)),
  );
  // Focus the non-modal dialog once the native top-layer surface has committed;
  // the browser owns focus, and no timers or subscriptions remain to clean up.
  useEffect(() => {
    if (surface && visible) surface.focus({ preventScroll: true });
  }, [surface, visible]);
  const locating = !anchor && !failed;
  return createPortal(
    <div
      className="ov-directory-tour-host"
      popover="manual"
      style={
        {
          width: position?.maxWidth,
          height: position?.layoutHeight,
          '--ov-ui-directory-tour-viewport-height': position
            ? `${position.maxHeight}px`
            : undefined,
          top: position?.top ?? 0,
          left: position?.left ?? 0,
          visibility: visible ? 'visible' : 'hidden',
        } as CSSProperties
      }
    >
      <div
        ref={setSurface}
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        aria-busy={locating}
        tabIndex={-1}
        className={['ov-directory-tour', className].filter(Boolean).join(' ')}
        style={{
          width: step.cardWidth,
          ...style,
        }}
      >
        {anchor && !unavailable && position?.visible && (
          <div
            className={
              elements.length > 1
                ? 'ov-directory-tour__highlight ov-directory-tour__highlight--group'
                : 'ov-directory-tour__highlight'
            }
            aria-hidden="true"
            style={{
              top: position.targetTop,
              left: position.targetLeft,
              width: position.targetWidth,
              height: position.targetHeight,
            }}
          />
        )}
        <TourCard
          step={step}
          index={index}
          total={steps.length}
          locale={locale}
          labels={labels}
          titleId={titleId}
          bodyId={bodyId}
          onClose={onClose}
          advance={() => advance(unavailable)}
          unavailable={unavailable}
          renderContent={anchor && !unavailable ? renderContent : undefined}
        />
      </div>
    </div>,
    document.body,
  );
}
