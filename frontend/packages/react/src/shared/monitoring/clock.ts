import type { SpanId, TraceId } from '../../client/monitoring/trace.js';

export const now = () => performance.now();
export const visibility = () =>
  document.visibilityState !== 'hidden'
    ? ('visible' as const)
    : ('hidden' as const);

function hex(bytes: number) {
  const data = new Uint8Array(bytes);
  if (typeof globalThis.crypto?.getRandomValues === 'function')
    crypto.getRandomValues(data);
  else for (let i = 0; i < bytes; i++) data[i] = Math.random() * 256;
  data[0] ||= 1;
  return Array.from(data, (value) => value.toString(16).padStart(2, '0')).join(
    '',
  );
}
export const traceId = () => hex(16) as TraceId;
export const spanId = () => hex(8) as SpanId;
export const eventId = () => hex(16);

declare const __OV_UI_VERSION__: string;
export const libraryVersion =
  typeof __OV_UI_VERSION__ === 'string' ? __OV_UI_VERSION__ : 'unknown';

/** Opaque local identity; the observed inputs are never retained in the token. */
export const opaqueIdentity = (..._inputs: unknown[]): object => ({});
