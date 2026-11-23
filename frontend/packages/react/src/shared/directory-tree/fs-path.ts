/**
 * 从根到目标路径之间的目录，按由浅到深排列；目标本身也包含在内——展开一个目录
 * 才能看到它的子节点。路径不在根之下时返回空数组。
 *
 * 拼接时保留根自带的斜杠：`viking://` 去掉末尾斜杠会变成 `viking:`，拼出来就是
 * `viking:/user` 这种不存在的路径。
 *
 * The directories from the root down to the target, shallowest first, including
 * the target itself: a directory must be open for its children to show. Returns an
 * empty array when the path is not under the root.
 *
 * The root keeps its own slashes when joined: stripping the trailing ones from
 * `viking://` yields `viking:`, and joining that produces a path like
 * `viking:/user` that does not exist.
 */
export function expandChain(rootUri: string, targetUri?: string): string[] {
  if (!targetUri) return [];
  // 根保持原样，只在末尾补齐一个斜杠：`viking://` 去掉末尾斜杠会变成
  // `viking:`，拼出来就是 `viking:/user` 这种不存在的路径。
  //
  // The root is kept as written, with one slash appended when missing: stripping
  // the trailing slashes from `viking://` yields `viking:`, and joining that
  // produces a path like `viking:/user` that does not exist.
  const base = rootUri.endsWith('/') ? rootUri : `${rootUri}/`;
  if (!targetUri.startsWith(base)) return [];
  const rest = targetUri.slice(base.length).split('/').filter(Boolean);
  return rest.map((_, index) => `${base}${rest.slice(0, index + 1).join('/')}`);
}
