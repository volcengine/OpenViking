/**
 * A validation problem as an i18n key under `gateway` plus its
 * interpolation values: render it with `t(issue.key, issue.values)`.
 */
export type FieldIssue = {
  key: `validation.${string}`
  values?: Record<string, string | number>
}

/** Field (or `field.subfield`) → first problem found. Empty when valid. */
export type ValidationErrors = Partial<Record<string, FieldIssue>>

/** Unit a numeric setting is measured in; labels live under `units.*`. */
export type Unit =
  | 'tokens'
  | 'seconds'
  | 'characters'
  | 'messages'
  | 'rounds'
  | 'bytes'
  | 'entries'

/** Bounds of a numeric setting, mirroring the gateway's model. */
export type NumberRule = {
  min?: number
  max?: number
  /** `min` itself is not allowed (the server uses `gt`). */
  exclusiveMin?: boolean
  integer?: boolean
  /** Empty (`null`) is allowed. */
  optional?: boolean
  /** Empty (`null`) is allowed and means no limit. */
  unlimited?: boolean
  step?: number
  unit?: Unit
}

/** Checks one number against its rule; `NaN` and `null` mean "empty". */
export function checkNumber(
  value: number | null | undefined,
  rule: NumberRule,
): FieldIssue | undefined {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return rule.optional || rule.unlimited
      ? undefined
      : { key: 'validation.required' }
  }
  if (!Number.isFinite(value)) return { key: 'validation.number' }
  if (rule.integer && !Number.isInteger(value)) {
    return { key: 'validation.integer' }
  }
  const { min, max, exclusiveMin } = rule
  const belowMin =
    min !== undefined && (exclusiveMin ? value <= min : value < min)
  const aboveMax = max !== undefined && value > max
  if ((belowMin || aboveMax) && min !== undefined && max !== undefined) {
    return {
      key: exclusiveMin ? 'validation.rangeExclusive' : 'validation.range',
      values: { min, max },
    }
  }
  if (belowMin) {
    return {
      key: exclusiveMin ? 'validation.greaterThan' : 'validation.min',
      values: { min },
    }
  }
  if (aboveMax) return { key: 'validation.max', values: { max } }
  return undefined
}

/** Checks a required text field. */
export function checkRequired(value: string): FieldIssue | undefined {
  return value.trim() ? undefined : { key: 'validation.required' }
}

/** True when a validator found nothing. */
export function isValid(errors: ValidationErrors): boolean {
  return Object.keys(errors).length === 0
}

/** Adds `issue` under `field` unless the check passed. */
export function collect(
  errors: ValidationErrors,
  field: string,
  issue: FieldIssue | undefined,
): void {
  if (issue && !(field in errors)) errors[field] = issue
}
