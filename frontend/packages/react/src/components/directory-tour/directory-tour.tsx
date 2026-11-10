import type { CSSProperties } from 'react';
import { useEffect, useId, useRef, useState } from 'react';
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
  const [attempt, retry] = useState(0);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const { anchor, elements, failed } = useTourAnchor(
    step.target,
    locateTarget,
    locateTimeout,
    attempt,
  );
  const position = useTourPosition(anchor, surface, placement, elements);
  useTourTheme(anchor, surface);
  const titleId = useId();
  const bodyId = useId();
  // Escape is a document event, so subscribe after commit and remove the current
  // handler on callback changes/unmount instead of keeping a stale host callback.
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && onClose) {
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
    const previous: (Element & { focus?: HTMLElement['focus'] }) | null =
      document.activeElement;
    return () => {
      if (previous?.isConnected) previous.focus?.({ preventScroll: true });
    };
  }, []);
  const visible = position?.visible;
  // Focus the non-modal dialog once the native top-layer surface has committed;
  // the browser owns focus, and no timers or subscriptions remain to clean up.
  useEffect(() => {
    if (surface && visible) surface.focus({ preventScroll: true });
  }, [surface, visible]);
  const unavailable = failed || position?.missing;
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
          visibility: position?.visible ? 'visible' : 'hidden',
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
          locating={locating}
          unavailable={Boolean(unavailable)}
          onClose={onClose}
          retry={() => retry((value) => value + 1)}
          advance={advance}
          renderContent={renderContent}
        />
      </div>
    </div>,
    document.body,
  );
}
