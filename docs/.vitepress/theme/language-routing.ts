export function localizedDocument(relativePath: string, locale: 'en' | 'zh', pages: string[]): string {
  const relative = relativePath.replace(/^(en|zh)\//, '');
  const candidate = `${locale}/${relative}`;
  const selected = /^(en|zh)\//.test(relativePath) && pages.includes(candidate)
    ? candidate : `${locale}/getting-started/01-introduction.md`;
  return '/' + selected.replace(/index\.md$/, '').replace(/\.md$/, '');
}
