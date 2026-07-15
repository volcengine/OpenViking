export function hasPageLlmsTxt(relativePath: string): boolean {
  return relativePath !== 'index.md' && !relativePath.endsWith('/index.md')
}
