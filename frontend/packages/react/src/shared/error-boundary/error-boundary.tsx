import { Component, createContext, useContext } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

import type { Locale } from '../locale.js';
import { useLocale } from '../locale-context.js';
import { enUS } from './locales/en-US.js';
import { zhCN } from './locales/zh-CN.js';
import type { ErrorFallback } from './types.js';

interface ErrorBoundaryProps {
  children?: ReactNode;
  message?: string;
  fallback?: ErrorFallback;
  resetKeys?: readonly unknown[];
  locale?: Locale;
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

  reset = () => this.setState({ failed: false });

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
  ...props
}: ErrorBoundaryProps) {
  const inheritedFallback = useContext(FallbackContext);
  const resolvedFallback =
    fallback === undefined ? inheritedFallback : fallback;
  const locale = useLocale(localeProp);
  const labels = locale === 'zh-CN' ? zhCN : enUS;
  return (
    <FallbackContext.Provider value={resolvedFallback}>
      <RenderBoundary
        {...props}
        fallback={
          resolvedFallback === undefined
            ? (reset) => (
                <div className="ov-error-boundary" role="alert">
                  <p className="ov-error-boundary__message">
                    {message ?? labels.failed}
                  </p>
                  <button
                    className="ov-error-boundary__reset"
                    type="button"
                    onClick={reset}
                  >
                    {labels.retry}
                  </button>
                </div>
              )
            : resolvedFallback
        }
      />
    </FallbackContext.Provider>
  );
}
