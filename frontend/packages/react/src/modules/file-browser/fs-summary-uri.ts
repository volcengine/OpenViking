/** 目录摘要层级：L0 是 `.abstract.md`，L1 是 `.overview.md`。 */
export type SummaryLevel = 'l0' | 'l1';

// 用 Map 而不是对象字面量：对象会继承 `constructor`、`toString` 这类属性，
// 名字恰好相同的普通资源会被误判成摘要文件。
// A Map rather than an object literal: an object inherits properties such as
// `constructor` and `toString`, so an ordinary resource with such a name would be
// mistaken for a summary file.
const SUMMARY_FILES = new Map<string, SummaryLevel>([
  ['.abstract.md', 'l0'],
  ['.overview.md', 'l1'],
]);

/**
 * 把指向目录摘要文件的 URI 拆成「所属目录 + 层级」。摘要文件是目录的内部产物，
 * 文件树的列表默认不返回它们，因此它们不能当作树节点定位，要落到所属目录上，
 * 再由预览切到对应层级。不是摘要文件时返回 undefined。
 *
 * Splits a URI that points at a directory summary file into its directory and
 * level. Summary files are internal to a directory and the tree listing omits them,
 * so they cannot be located as tree rows: navigation lands on the owning directory
 * and the preview switches to that level. Returns undefined for any other URI.
 */
export function parseSummaryUri(uri: string) {
  const index = uri.lastIndexOf('/');
  const level = SUMMARY_FILES.get(uri.slice(index + 1));
  const directory = uri.slice(0, index);
  if (!level || !/^[a-z][a-z0-9+.-]*:\/\/.+/i.test(directory)) return undefined;
  return { directory, level };
}
