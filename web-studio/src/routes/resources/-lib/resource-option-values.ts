export function parseDelimitedValues(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function parseOptionalInteger(
  value: string,
  minimum: number,
): number | undefined {
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number(trimmed)
  if (!Number.isInteger(parsed) || parsed < minimum) return undefined
  return parsed
}

export function isOptionalIntegerValid(
  value: string,
  minimum: number,
): boolean {
  return !value.trim() || parseOptionalInteger(value, minimum) !== undefined
}

const RESOURCE_TAG_PATTERN = /^[^=,\s]+=[^=,\s]+$/

export function parseResourceTags(value: string): {
  tags: string[]
  valid: boolean
} {
  const tags = Array.from(new Set(parseDelimitedValues(value)))
  return {
    tags,
    valid: tags.every((tag) => RESOURCE_TAG_PATTERN.test(tag)),
  }
}
