export function sourceUris(value: string) {
  return [
    ...new Set(
      value
        .split('\n')
        .map((uri) => uri.trim().replace(/\/$/, ''))
        .filter(Boolean),
    ),
  ]
}

export function locationLabel(uri: string, userRoot: string) {
  const clean = uri.replace(/\/$/, '')
  const mine = userRoot.replace(/\/$/, '')
  if (clean === mine || clean.startsWith(`${mine}/`))
    return {
      scope: 'personalSpace' as const,
      path: clean.slice(mine.length + 1),
    }
  if (clean === 'viking://resources' || clean.startsWith('viking://resources/'))
    return {
      scope: 'sharedSpace' as const,
      path: clean.slice('viking://resources/'.length),
    }
  return { scope: 'otherLocation' as const, path: clean }
}

export function targetError(uri: string) {
  if (!uri.trim()) return 'targetRequired' as const
  if (
    !uri.startsWith('viking://') ||
    /[\\\r\n]/.test(uri) ||
    uri
      .slice(9)
      .split('/')
      .some((part) => part === '.' || part === '..')
  )
    return 'invalidUri' as const
  if (
    /^viking:\/\/(?:resources|user\/[^/]+\/resources)\/?$/.test(uri) ||
    uri === 'viking://' ||
    uri.startsWith('viking://system/')
  )
    return 'targetRootInvalid' as const
  return null
}
