export function hasPageLlmsTxt(relativePath: string): boolean {
  return relativePath !== 'index.md' && !relativePath.endsWith('/index.md')
}

export function pageLlmsTxtPath(relativePath: string): string {
  return `/${relativePath.replace(/\.md$/, '')}/llms.txt`
}

export function absoluteLlmsTxtUrl(path: string, origin: string): string {
  return new URL(path, origin).href
}
