import type { MonitoringBinding } from './binding.js';
import type { MonitoringSpan } from './trace/span.js';

export type MonitoringData = Map<
  string,
  WeakMap<object, readonly MonitoringSpan[]>
>;
const scope = (binding: MonitoringBinding) => {
  if (binding.dataScope) return binding.dataScope();
  const source = binding.source();
  return source.module?.instanceId ?? source.component.instanceId;
};
/** Metadata belongs to this Provider and module; it never enters view models. */
export function tagData(
  binding: MonitoringBinding | undefined,
  data: object | undefined,
  inputs: readonly (MonitoringSpan | undefined)[],
) {
  if (!binding?.active() || !binding.controller.options || !data) return;
  const spans = inputs.filter(
    (span): span is MonitoringSpan => !!span?.available(),
  );
  if (!spans.length) return;
  const scopes = (binding.controller.data ??= new Map<
    string,
    WeakMap<object, readonly MonitoringSpan[]>
  >());
  const key = scope(binding);
  if (!key) return;
  let values = scopes.get(key);
  if (!values)
    scopes.set(
      key,
      (values = new WeakMap<object, readonly MonitoringSpan[]>()),
    );
  values.set(data, spans);
}
export function readData(
  binding: MonitoringBinding | undefined,
  values: readonly unknown[],
) {
  if (!binding?.controller.options || !binding.controller.data) return [];
  const key = scope(binding);
  if (!key) return [];
  const data = binding.controller.data.get(key);
  return [
    ...new Set(
      values.flatMap((value) =>
        value && typeof value === 'object' ? (data?.get(value) ?? []) : [],
      ),
    ),
  ].filter((span) => span.available());
}

/** Associate the exact model objects subsequently passed to view components. */
export function tagModel(
  binding: MonitoringBinding | undefined,
  value: unknown,
  span?: MonitoringSpan,
) {
  if (!span || !value || typeof value !== 'object') return;
  tagData(binding, value, [span]);
  for (const child of Object.values(value as Record<string, unknown>))
    if (child && typeof child === 'object') tagData(binding, child, [span]);
}
