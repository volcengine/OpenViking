import type { AdminAccount } from '#/lib/admin'

export const DEFAULT_ACCOUNT_ID = 'default'
export const DEFAULT_USER_ID = 'default'

export function uniqueOptions(
  values: readonly string[],
  fallback: string,
): string[] {
  const seen = new Set<string>()
  const result: string[] = []

  for (const rawValue of [fallback, ...values]) {
    const value = rawValue.trim()
    if (!value || seen.has(value)) {
      continue
    }
    seen.add(value)
    result.push(value)
  }

  return result
}

export function compareAccountId(left: string, right: string): number {
  const normalizedLeft = left.toLowerCase()
  const normalizedRight = right.toLowerCase()
  if (normalizedLeft < normalizedRight) {
    return -1
  }
  if (normalizedLeft > normalizedRight) {
    return 1
  }
  return left < right ? -1 : left > right ? 1 : 0
}

export function sortedAccountIds(
  values: readonly string[],
  fallback: string,
): string[] {
  return uniqueOptions([...values, fallback], '').sort(compareAccountId)
}

export function sortedAccounts(
  accounts: readonly AdminAccount[],
): AdminAccount[] {
  return [...accounts].sort((left, right) =>
    compareAccountId(left.accountId, right.accountId),
  )
}
