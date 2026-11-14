import { TourStep } from './tour-step.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { useAnalyticsRef } from '../../shared/analytics/context.js';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { DirectoryTourProps } from './types.js';

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
  const analytics = useAnalyticsRef();
  const [index, setIndex] = useState(props.defaultStep ?? 0);
  const [completed, setCompleted] = useState(false);
  const skipped = useRef(new Set<string>());
  const current = currentStep ?? index;
  const startParams = useCallback(() => ({ step_index: current }), [current]);
  const startExposure = useExposure(
    'directory_tour_start',
    undefined,
    startParams,
  );
  const step = steps[current];
  const advance = useCallback(
    (skip: boolean) => {
      if (!step || completed) return;
      if (skip) skipped.current.add(step.id);
      if (current === steps.length - 1) {
        analytics.current?.emit('directory_tour_complete', () => ({
          step_index: current,
        }));
        setCompleted(true);
        onComplete?.({ skippedStepIds: [...skipped.current] });
      } else {
        if (currentStep === undefined) setIndex(current + 1);
        onStepChange?.(current + 1);
      }
    },
    [
      analytics,
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
      startExposure={startExposure}
      {...props}
      step={step}
      index={current}
      advance={advance}
    />
  );
}
