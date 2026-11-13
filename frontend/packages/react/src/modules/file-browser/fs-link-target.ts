export type LinkTarget =
  { kind: 'resource'; uri: string } | { kind: 'external'; url: string };

/**
 * 解析 Markdown 正文里点击的链接。`viking://` 链接和相对路径落到资源上，
 * 相对路径以当前文档的 URI 为基准；http(s) 与 mailto 交给浏览器新窗口打开；
 * 页内锚点和其它协议返回 undefined，由调用方忽略。
 *
 * Resolves a link clicked in a Markdown body. `viking://` links and relative paths
 * resolve to resources, relative ones against the current document's URI; http(s)
 * and mailto go to a new browser window; in-page anchors and other schemes return
 * undefined for the caller to ignore.
 */
export function resolveLinkTarget(
  raw: string,
  baseUri: string,
): LinkTarget | undefined {
  if (!raw || raw.startsWith('#')) return undefined;
  let url: URL;
  try {
    url = new URL(raw, baseUri);
  } catch {
    return undefined;
  }
  if (url.protocol === 'viking:') {
    // 锚点不属于资源名：`.overview.md#section` 要按 `.overview.md` 识别。
    // The anchor is not part of the resource name: `.overview.md#section` must be
    // recognised as `.overview.md`.
    url.hash = '';
    return { kind: 'resource', uri: decodeURI(url.href) };
  }
  if (['http:', 'https:', 'mailto:'].includes(url.protocol))
    return { kind: 'external', url: url.href };
  return undefined;
}
