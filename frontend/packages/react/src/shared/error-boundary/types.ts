import type { ReactNode } from 'react';

/** A render function receives the retry action for the current failed region. */
export type ErrorFallback = ReactNode | ((reset: () => void) => ReactNode);
