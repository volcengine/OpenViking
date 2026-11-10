/** 路径分隔符；`viking://` 这类带协议前缀的路径同样按 `/` 拆分。 */
const SEPARATOR = '/';

export interface PathSegment {
  /** 该段的展示文本，不含协议前缀。 */
  label: string;
  /** 点击该段时要打开的完整 URI。 */
  uri: string;
  /** 是否首段，渲染时在它前面补上协议前缀。 */
  isFirst: boolean;
}

/**
 * 把完整路径拆成可逐级打开的祖先段，最后一段（文件名）不包含在内。
 *
 * 带协议前缀时首段带 `isFirst` 标记，渲染时补上前缀：`viking://a/b/c.md` 拆成
 * `a`、`b`，首段显示为 `viking://a`。
 *
 * 绝对路径（`/a/b/c.md`）保留开头的斜杠：丢掉它祖先会变成相对路径 `a`、
 * `a/b`，宿主按相对路径解析就跳到了错误的目录。相对路径（`a/b/c.md`）保持
 * 原样，不加斜杠。
 *
 * Splits a full path into the ancestor levels that can be opened one by one,
 * excluding the trailing file name. The first segment carries `isFirst` so the
 * renderer can prepend the scheme: `viking://a/b/c.md` yields `a` and `b`, the
 * first rendering as `viking://a`.
 *
 * An absolute path (`/a/b/c.md`) keeps its leading slash: dropping it turns the
 * ancestors into relative paths (`a`, `a/b`) that a host resolves against the
 * wrong directory. A relative path (`a/b/c.md`) is left as-is with no slash added.
 */
export function splitPathSegments(path?: string): PathSegment[] {
  const trimmed = path?.trim();
  if (!trimmed) return [];

  const schemeIndex = trimmed.indexOf('://');
  const scheme = schemeIndex >= 0 ? trimmed.slice(0, schemeIndex + 3) : '';
  const body = schemeIndex >= 0 ? trimmed.slice(schemeIndex + 3) : trimmed;
  const parts = body.split(SEPARATOR).filter(Boolean);
  if (parts.length <= 1) return [];

  // 无协议前缀且以 `/` 开头的是绝对路径，祖先必须保留这个根斜杠。
  const root = scheme === '' && body.startsWith(SEPARATOR) ? SEPARATOR : '';
  const segments: PathSegment[] = [];
  let uri = scheme;

  parts.slice(0, -1).forEach((part, index) => {
    uri = index === 0 ? `${scheme}${root}${part}` : `${uri}${SEPARATOR}${part}`;
    segments.push({ label: part, uri, isFirst: index === 0 });
  });

  return segments;
}

/** 取路径里的文件名，用于面包屑末段与标题回落。 */
export function getPathFileName(path?: string): string | undefined {
  const trimmed = path?.trim();
  if (!trimmed) return undefined;
  const parts = trimmed.split(SEPARATOR).filter(Boolean);
  return parts[parts.length - 1];
}
