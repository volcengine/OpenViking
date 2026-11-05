/** Match namespace segments, never arbitrary substrings in document names. */
export function isExperienceDirectory(uri: string): boolean {
  return /^viking:\/\/(?:user\/[^/]+|agent)(?:\/peers\/[^/]+)?\/memories\/experiences(?:\/|$)/.test(
    uri,
  )
}

export function isExperienceFile(uri: string): boolean {
  return isExperienceDirectory(uri) && /\.md$/i.test(uri)
}

export function sessionIdFromDirectory(uri: string): string | null {
  return /^viking:\/\/user\/[^/]+\/sessions\/([^/]+)\/?$/.exec(uri)?.[1] ?? null
}

export function usesWorkbenchDirectoryView(uri: string): boolean {
  return (
    uri === 'viking://' ||
    isExperienceDirectory(uri) ||
    /^viking:\/\/user\/[^/]+\/sessions\/?$/.test(uri) ||
    sessionIdFromDirectory(uri) !== null
  )
}
