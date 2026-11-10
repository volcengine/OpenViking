/**
 * 记忆文件末尾的 `MEMORY_FIELDS` 注释：服务端写给系统的内部元数据（版本、来源
 * 追踪 id、记忆类型），不属于正文，也不该出现在预览里。
 *
 * 正常读取（`content/read` 不带 `raw`）由服务端先剥掉它再按 offset/limit 切片，
 * 这里的处理是同一规则的兜底：宿主可能通过自定义传输层或网关让请求带上
 * `raw=true`，那条路径返回的是原始字节，尾注会原样回来。
 *
 * 规则与后端保持一致，而不是「看到同形注释就删」：
 *
 * - 只认**位于文件末尾**的那一块（其后只剩空白），正文中间出现的同形注释属于
 *   内容本身，例如资源文档里举例的这段标记；
 * - 连续多块尾注一起剥掉；
 * - 只在记忆命名空间生效，资源与技能文件不经过这一步。
 *
 * The `MEMORY_FIELDS` comment at the end of a memory file: internal metadata the
 * server writes for its own bookkeeping (version, trace ids, memory type). It is not
 * part of the body and must not reach the preview.
 *
 * A normal read (`content/read` without `raw`) has the server strip it before slicing
 * by offset/limit; the handling here is the same rule as a fallback. A host can make
 * the request carry `raw=true` through a custom transport or a gateway, and that path
 * returns the stored bytes with the trailer intact.
 *
 * The rule mirrors the backend rather than "delete anything shaped like this":
 *
 * - only the block that ends the file counts (nothing but whitespace after it); a
 *   same-shaped comment in the middle is content, such as one quoted in a resource
 *   document;
 * - consecutive trailing blocks are all removed;
 * - it applies to the memory namespace only — resource and skill files never go
 *   through it.
 */
const MEMORY_FIELDS_COMMENT = /<!--\s*MEMORY_FIELDS\b[\s\S]*?\s*-->/g;

/** 拆分 Viking URI 的路径段，忽略查询串与末尾斜杠。Path segments of a Viking URI. */
function uriParts(uri: string): string[] {
  const [beforeQuery = ''] = uri.split('?');
  const path = beforeQuery.replace(/\/+$/, '');
  if (!path.startsWith('viking://')) return [];
  return path.slice('viking://'.length).split('/').filter(Boolean);
}

/**
 * 该 URI 是否落在记忆命名空间。`viking://~/memories/...` 这类当前用户别名按
 * `user/<uid>` 展开后再判断，与请求边界上的解析一致。
 *
 * Whether the URI sits in the memory namespace. The `viking://~/memories/...` home
 * alias is expanded to `user/<uid>` first, matching how the request boundary resolves
 * it.
 */
export function isMemoryUri(uri: string): boolean {
  const parts = uriParts(uri);
  if (parts.length < 3) return false;
  if (parts[0] !== 'user' && parts[0] !== '~') return false;
  // 别名少一段 uid，补上后路径位置才与规范 URI 对齐。
  // The alias omits the uid segment, so insert it to line up with a canonical URI.
  const scoped = parts[0] === '~' ? ['user', '~', ...parts.slice(1)] : parts;
  if (scoped[2] === 'memories') return true;
  return (
    scoped.length >= 5 && scoped[2] === 'peers' && scoped[4] === 'memories'
  );
}

/**
 * 剥掉文件末尾的 `MEMORY_FIELDS` 尾注；没有尾注时原样返回。
 *
 * 每次循环只认「其后全是空白」的最后一块，剥完再判断一次，因为连续多块尾注
 * 需要逐个处理；正文中间的同形注释因此不会被误删。
 *
 * Removes the trailing `MEMORY_FIELDS` blocks; returns the text unchanged when there
 * is none.
 *
 * Each pass takes only the last block that nothing but whitespace follows, then checks
 * again, because consecutive trailing blocks need one pass each; a same-shaped comment
 * in the middle of the body is therefore never removed.
 */
export function stripMemoryFieldsTrailer(text: string): string {
  let visible = text;
  for (;;) {
    let start = -1;
    MEMORY_FIELDS_COMMENT.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = MEMORY_FIELDS_COMMENT.exec(visible)) !== null) {
      const after = visible.slice(match.index + match[0].length);
      if (after.trim() === '') start = match.index;
    }
    if (start < 0) return visible;
    visible = visible.slice(0, start).trimEnd();
  }
}
