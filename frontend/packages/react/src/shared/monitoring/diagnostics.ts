import type {
  OpenVikingMonitoringOptions,
  SanitizedDiagnostics,
} from '../../client/monitoring/types.js';

type Raw = Parameters<
  NonNullable<OpenVikingMonitoringOptions['diagnostics']>['transform']
>[0];
const bytes = (text: string) => new TextEncoder().encode(text).byteLength;

export function diagnostics(options: OpenVikingMonitoringOptions, raw: Raw) {
  if (!options.diagnostics) return;
  try {
    const transformed = options.diagnostics.transform(raw);
    if (!transformed) return;
    if ('then' in transformed && typeof transformed.then === 'function') {
      void Promise.resolve(transformed).catch(() => {});
      return;
    }
    // Round-trip the result, never retain a host-owned mutable diagnostic object.
    const output = JSON.parse(
      JSON.stringify(transformed),
    ) as SanitizedDiagnostics;
    const result: SanitizedDiagnostics = {};
    for (const key of ['requestBody', 'responseBody'] as const) {
      const value = output[key];
      if (value !== undefined && bytes(JSON.stringify(value)) <= 4096)
        Object.assign(result, { [key]: value });
    }
    const detail = {
      message: typeof output.message === 'string' ? output.message : undefined,
      stack: typeof output.stack === 'string' ? output.stack : undefined,
      componentStack:
        typeof output.componentStack === 'string'
          ? output.componentStack
          : undefined,
    };
    if (bytes(JSON.stringify(detail)) <= 16384) Object.assign(result, detail);
    return result;
  } catch {
    // A broken redactor must neither disclose raw input nor change the UI result.
  }
}
