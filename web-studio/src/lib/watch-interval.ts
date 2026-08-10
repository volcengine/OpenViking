export function parsePositiveMinutes(value: number | string): number | null {
  const minutes = Number(value)
  return Number.isFinite(minutes) && minutes > 0 ? minutes : null
}
