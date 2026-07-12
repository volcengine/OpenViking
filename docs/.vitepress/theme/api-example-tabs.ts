export interface ExampleLanguage {
  key: string
  label: string
  kind: 'language' | 'response'
}

export function exampleLanguage(label: string): ExampleLanguage | undefined {
  const normalized = label.trim().replace(/[：:]$/, '').trim()
  const languageHeading = (name: string) =>
    new RegExp(`^(?:${name})(?:\\s*\\([^)]*\\))?$`, 'i').test(normalized)

  if (languageHeading('python sdk')) return { key: 'python', label: 'Python', kind: 'language' }
  if (languageHeading('typescript sdk|javascript sdk')) {
    return { key: 'typescript', label: 'TypeScript', kind: 'language' }
  }
  if (languageHeading('go sdk')) return { key: 'go', label: 'Go', kind: 'language' }
  if (languageHeading('http api')) return { key: 'http', label: 'HTTP', kind: 'language' }
  if (languageHeading('cli')) return { key: 'cli', label: 'CLI', kind: 'language' }
  return undefined
}

function responseKey(value: string): string {
  const normalized = value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-|-$/g, '')
  return `response-${normalized}`
}

export function shouldSynchronizeExampleTabs(kind: string | undefined): boolean {
  return kind === 'language'
}

export function responseExample(label: string): ExampleLanguage | undefined {
  const normalized = label.trim().replace(/[：:]$/, '').trim()
  const http = normalized.match(/^HTTP API (?:Response|响应)(.*)$/i)
  if (http) {
    const wait = http[1].match(/wait\s*=\s*(true|false)/i)?.[1]?.toLowerCase()
    return {
      key: wait === 'false' ? 'response-http-async' : 'response-http',
      label: wait ? `HTTP (wait=${wait})` : 'HTTP',
      kind: 'response'
    }
  }
  const cli = normalized.match(/^CLI (?:Response|响应)(.*)$/i)
  if (cli) {
    const json = /json/i.test(cli[1])
    return { key: json ? 'response-cli-json' : 'response-cli', label: json ? 'CLI JSON' : 'CLI', kind: 'response' }
  }
  const variant = normalized.match(/^(?:Response|响应)\s*[（(]([^）)]+)[）)]$/i)
  if (variant) return { key: responseKey(variant[1]), label: variant[1], kind: 'response' }
  if (/^(?:Synchronous response|同步响应)/i.test(normalized)) {
    return { key: 'response-sync', label: /同步/.test(normalized) ? '同步' : 'Synchronous', kind: 'response' }
  }
  if (/^(?:Asynchronous response|异步响应)/i.test(normalized)) {
    return { key: 'response-async', label: /异步/.test(normalized) ? '异步' : 'Asynchronous', kind: 'response' }
  }
  const example = normalized.match(/^(?:Response Example|响应示例)\s*[（(]([^）)]+)[）)]$/i)
  if (example) {
    return { key: responseKey(example[1]), label: example[1], kind: 'response' }
  }
  return undefined
}

export function exampleHeading(label: string): ExampleLanguage | undefined {
  return exampleLanguage(label) ?? responseExample(label)
}

export function isSharedSectionLabel(label: string): boolean {
  const normalized = label.trim().replace(/[：:]$/, '').trim()
  return exampleHeading(label) === undefined && /^(?:(?:error )?response\b|响应|result fields?\b|notes?\b|cli override flags?\b|mcp\b|返回|说明)/i.test(
    normalized
  )
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
