import { useMonitoringFlow } from '../monitoring/flow-context.js';
import type {
  ComponentName,
  RegionByComponent,
} from '../../client/monitoring/source.js';
import { useMonitoringBinding } from '../monitoring/context.js';
import type { MonitoringFrame } from '../monitoring/frame.js';
import { Component, createContext, useContext } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

import type { Locale } from '../locale.js';
import { Button } from '../primitives/button/button.js';
import { Empty } from '../primitives/empty/empty.js';
import { useLocale } from '../locale-context.js';
import { enUS } from './locales/en-US.js';
import { zhCN } from './locales/zh-CN.js';
import type { ErrorFallback } from './types.js';

interface ErrorBoundaryProps {
  monitoringRegion?: RegionByComponent[ComponentName];
  children?: ReactNode;
  message?: string;
  fallback?: ErrorFallback;
  resetKeys?: readonly unknown[];
  locale?: Locale;
  onReset?: () => void;
  onError?: (error: unknown, info: ErrorInfo) => void;
}

const FallbackContext =
  createContext<ErrorBoundaryProps['fallback']>(undefined);

const changed = (
  before: readonly unknown[] = [],
  after: readonly unknown[] = [],
) =>
  before.length !== after.length ||
  before.some((value, index) => !Object.is(value, after[index]));

class RenderBoundary extends Component<
  ErrorBoundaryProps,
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    if (this.props.onError) this.props.onError(error, info);
    else console.error(error, info.componentStack);
  }

  componentDidUpdate(previous: ErrorBoundaryProps, state: { failed: boolean }) {
    // Only reset an already-failed tree when its meaningful identity changes.
    // A failure during the identity-changing render must not trigger a retry loop.
    if (
      this.state.failed &&
      state.failed &&
      changed(previous.resetKeys, this.props.resetKeys)
    ) {
      this.reset();
    }
  }

  reset = () => {
    this.props.onReset?.();
    this.setState({ failed: false });
  };

  render() {
    if (!this.state.failed) return this.props.children;
    const { fallback } = this.props;
    return typeof fallback === 'function' ? fallback(this.reset) : fallback;
  }
}

/** Internal rendering isolation. Requests and event handlers keep their own error handling. */
export function ErrorBoundary({
  locale: localeProp,
  message,
  fallback,
  monitoringRegion,
  ...props
}: ErrorBoundaryProps) {
  const flow = useMonitoringFlow();
  const monitoring = useMonitoringBinding() as MonitoringFrame | undefined;
  const inheritedFallback = useContext(FallbackContext);
  const resolvedFallback =
    fallback === undefined ? inheritedFallback : fallback;
  const locale = useLocale(localeProp);
  const labels = locale === 'zh-CN' ? zhCN : enUS;
  return (
    <FallbackContext.Provider value={resolvedFallback}>
      <RenderBoundary
        {...props}
        onError={(error, info) => {
          monitoring?.renderError(
            error,
            info.componentStack,
            monitoringRegion,
            flow,
          );
          if (props.onError) props.onError(error, info);
          else console.error(error, info.componentStack);
        }}
        fallback={
          resolvedFallback === undefined
            ? (reset) => (
                <Empty
                  size="compact"
                  illustration="crash"
                  role="alert"
                  title={message ?? labels.failed}
                  className="ov-error-boundary"
                  action={
                    <Button
                      type="secondary"
                      size="small"
                      htmlType="button"
                      onClick={reset}
                    >
                      {labels.retry}
                    </Button>
                  }
                />
              )
            : resolvedFallback
        }
      />
    </FallbackContext.Provider>
  );
}
