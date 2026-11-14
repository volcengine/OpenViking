/** Parameter descriptors shared by the typed event catalogue and its documentation. */
export const operation = { operation_id: 'string' } as const;
export const completed = { ...operation, duration_ms: 'number' } as const;
export const failed = {
  ...completed,
  error_kind: ['network', 'http', 'business', 'permission', 'unknown'],
} as const;
export const search = {
  ...operation,
  mode: ['find', 'search', 'grep', 'glob'],
  has_filters: 'boolean',
} as const;

type Field = 'string' | 'number' | 'boolean' | readonly string[];
export type AnalyticsFields = Readonly<Record<string, Field>>;
type Value<T> = T extends readonly string[]
  ? T[number]
  : T extends 'number'
    ? number
    : T extends 'boolean'
      ? boolean
      : string;
export type AnalyticsParams<T extends AnalyticsFields> = keyof T extends never
  ? Readonly<Record<string, never>>
  : { readonly [K in keyof T]: Value<T[K]> };
