import type { CSSProperties } from 'react';

/** Preserve a caller's stricter length/percentage cap inside the visual viewport. */
export function viewportMaximum(
  caller: CSSProperties['maxWidth'],
  viewport: number | undefined,
): CSSProperties['maxWidth'] {
  if (viewport === undefined) return caller;
  if (
    caller === undefined ||
    caller === 'none' ||
    caller === 'initial' ||
    caller === 'unset'
  )
    return viewport;
  const limit = typeof caller === 'number' ? `${caller}px` : caller;
  return `min(${limit}, ${viewport}px)`;
}
