export interface ExampleLanguage {
  key: string
  label: string
}

export function exampleLanguage(label: string): ExampleLanguage | undefined {
  const normalized = label.trim().replace(/[：:]$/, '').trim()
  const languageHeading = (name: string) =>
    new RegExp(`^(?:${name})(?:\\s*\\([^)]*\\))?$`, 'i').test(normalized)

  if (languageHeading('python sdk')) return { key: 'python', label: 'Python' }
  if (languageHeading('typescript sdk|javascript sdk')) {
    return { key: 'typescript', label: 'TypeScript' }
  }
  if (languageHeading('go sdk')) return { key: 'go', label: 'Go' }
  if (languageHeading('http api')) return { key: 'http', label: 'HTTP' }
  if (languageHeading('cli')) return { key: 'cli', label: 'CLI' }
  return undefined
}

export function isSharedSectionLabel(label: string): boolean {
  return exampleLanguage(label) === undefined
}

export function isApiReferencePath(path: string): boolean {
  return /(?:^|\/)(?:en|zh)\/api(?:\/|$)/.test(path)
}

export function preferredLanguage(
  storedLanguage: string | null,
  currentLanguage: string | undefined,
  firstAvailableLanguage: string
): string {
  return storedLanguage ?? currentLanguage ?? firstAvailableLanguage
}
