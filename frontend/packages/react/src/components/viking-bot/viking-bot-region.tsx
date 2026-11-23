import { useRenderErrors } from './labels.js';
import type { ReactNode } from 'react';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';

/** Keep session and draft state above independently rendered conversation regions. */
export function VikingBotRegion({
  name,
  resetKey,
  children,
}: {
  name?: 'header' | 'body' | 'footer';
  resetKey?: string;
  children?: ReactNode;
}) {
  const messages = useRenderErrors();
  const message = messages[name === 'body' ? 'list' : (name ?? 'composer')];
  const content = (
    <ErrorBoundary message={message} resetKeys={[resetKey]}>
      {children}
    </ErrorBoundary>
  );
  return name ? (
    <div className={`ov-viking-bot__${name}`}>{content}</div>
  ) : (
    content
  );
}
