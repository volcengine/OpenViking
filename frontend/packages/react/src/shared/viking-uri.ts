/**
 * 把 `viking://` 地址拆成小写路径段，忽略协议、查询串与末尾斜杠。
 *
 * Splits a `viking://` address into lower-cased segments, ignoring the scheme,
 * query string and trailing slashes.
 */
export function splitVikingUri(uri: string): string[] {
  const withoutScheme = uri.replace(/^viking:\/*/i, '');
  const withoutQuery = withoutScheme.split(/[?#]/)[0] ?? '';

  return withoutQuery
    .split('/')
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => segment.toLowerCase());
}
