/** Keep the hosted identity compact without inferring the service edition. */
export function formatHostedIdentity(
  accountId?: string | null,
  userId?: string | null,
): string | null {
  const account = accountId?.trim() ?? ''
  const user = userId?.trim() ?? ''
  if (!account || !user) return null
  return account === 'default' && user === 'default'
    ? 'default'
    : `${account}/${user}`
}
