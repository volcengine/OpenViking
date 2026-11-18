export function parseDelimitedValues(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function parseOptionalNumber(value: string): number | undefined {
  const trimmed = value.trim()
  if (!trimmed) return undefined
  return Number(trimmed)
}

export function parseResourceTags(value: string): string[] {
  return Array.from(new Set(parseDelimitedValues(value)))
}
