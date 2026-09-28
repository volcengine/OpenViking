/** Used unchanged by the blocking head script and the entry contract tests. */
export function docsLanguageEntry(href, base, preference) {
  const url = new URL(href);
  const root = '/' + base.split('/').filter(Boolean).join('/');
  const prefix = root === '/' ? '/' : root + '/';
  const relative = url.pathname === root ? '' : url.pathname.startsWith(prefix) ? url.pathname.slice(prefix.length) : null;
  if (relative !== '' && relative !== 'index.html') return null;
  const locale = preference.resolve(preference.parse(url.searchParams.get('lang')) || null, true);
  url.pathname = prefix + locale + '/';
  return url.href;
}
