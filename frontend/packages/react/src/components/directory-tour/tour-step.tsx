import type { CSSProperties } from 'react';
import { useCallback, useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DirectoryTourProps, DirectoryTourStep } from './types.js';
import { useTourAnchor } from './use-tour-anchor.js';
import { useTourPosition } from './use-tour-position.js';
import { useTourTheme } from './use-tour-theme.js';
import { TourCard } from './tour-card.js';
import { useLocale } from '../../shared/locale-context.js';
import { useAnalyticsRef } from '../../shared/analytics/context.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';

export function TourStep({
  step,
  startExposure,
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
  startExposure?: (node: HTMLElement | null) => void;
  index: number;
  advance: (skip: boolean) => void;
}) {
  const analytics = useAnalyticsRef();
  const reportClose = useCallback(() => {
    analytics.current?.emit('directory_tour_close', () => ({
      step_index: index,
    }));
    onClose?.();
  }, [analytics, index, onClose]);
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
        reportClose();
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose, reportClose]);
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
  const stepParams = useCallback(() => ({ step_index: index }), [index]);
  const stepExposure = useExposure(
    'directory_tour_step_show',
    index,
    stepParams,
    visible,
  );
  const surfaceRef = useCallback(
    (node: HTMLDivElement | null) => {
      setSurface(node);
      stepExposure?.(node);
      if (visible) startExposure?.(node);
    },
    [stepExposure, startExposure, visible],
  );
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
        ref={surfaceRef}
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
          onClose={onClose ? reportClose : undefined}
          advance={() => advance(unavailable)}
          unavailable={unavailable}
          renderContent={anchor && !unavailable ? renderContent : undefined}
        />
      </div>
    </div>,
    document.body,
  );
}
