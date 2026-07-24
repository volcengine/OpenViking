export type TaskTimestamp = {
  created_at?: number | string
  created_at_iso?: string
  updated_at?: number | string
  updated_at_iso?: string
}

export function getTaskDate(task: TaskTimestamp): Date | undefined {
  const value =
    task.created_at_iso ??
    task.updated_at_iso ??
    task.created_at ??
    task.updated_at
  if (value === undefined) return undefined

  const numericValue =
    typeof value === 'number'
      ? value
      : value.trim() !== '' && Number.isFinite(Number(value))
        ? Number(value)
        : undefined
  const normalizedValue =
    numericValue === undefined
      ? value
      : Math.abs(numericValue) < 1_000_000_000_000
        ? numericValue * 1_000
        : numericValue
  const date = new Date(normalizedValue)
  return Number.isNaN(date.getTime()) ? undefined : date
}
