import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { useRenderErrors } from './labels.js';
import { ExperienceDashboardContent } from './experience-dashboard-content.js';
import type { ExperienceDashboardProps } from './types.js';

export function ExperienceDashboard(props: ExperienceDashboardProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).dashboard}
      locale={props.locale}
      resetKeys={[props.uri]}
    >
      <ExperienceDashboardContent key={props.uri} {...contentProps} />
    </ErrorBoundary>
  );
}
