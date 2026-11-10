/**
 * 与服务端一致的行切分。
 *
 * `content/read` 的 `offset` / `limit` 是行号，服务端用 Python 的
 * `str.splitlines(keepends=True)` 切分。前端自己切行时必须用同一套边界，否则两边
 * 数出的行数不同：单独一个 `\r` 在 Python 里是一行结束，`split('\n')` 却看不见它；
 * 而「2000 行、每行以 `\n` 结尾」的文本在 `split('\n')` 下会多出一个末尾空串，被
 * 当成第 2001 行。
 *
 * Line splitting that matches the server.
 *
 * `offset` / `limit` on `content/read` are line numbers, and the server splits with
 * Python's `str.splitlines(keepends=True)`. A client that slices lines itself has to
 * use the same boundaries or the two disagree on how many lines there are: a lone
 * `\r` ends a line in Python while `split('\n')` does not see it, and a text of 2000
 * lines each ending in `\n` gains a trailing empty element under `split('\n')` that
 * looks like a 2001st line.
 */

/**
 * Python 视为换行结束的字符码，与 `str.splitlines` 的集合一致。
 *
 * `\r\n` 是两码连写，按一个分隔符处理（见 `splitLines`）；单独出现时各算一个。
 *
 * 用字符码而不是正则：其中几个是不可打印的控制字符，正则字面量里写出来会被
 * `no-control-regex` 拦下，也难读。
 *
 * The character codes Python treats as line ends, matching `str.splitlines`.
 *
 * `\r\n` is two codes and counts as one separator (see `splitLines`); either alone
 * counts as its own.
 *
 * Codes rather than a regex: several are unprintable control characters, which
 * `no-control-regex` rejects inside a literal and which read poorly anyway.
 */
const LINE_BREAK_CODES = new Set([
  0x0a, // \n
  0x0b, // \v
  0x0c, // \f
  0x0d, // \r
  0x1c, // 文件分隔符 File separator
  0x1d, // 组分隔符 Group separator
  0x1e, // 记录分隔符 Record separator
  0x85, // NEL
  0x2028, // 行分隔符 Line separator
  0x2029, // 段分隔符 Paragraph separator
]);

/**
 * 按服务端的分隔符切行，每行保留自己的换行符；末尾的换行符不产生额外空行。
 *
 * 空串返回空数组，与服务端一致——`''.splitlines()` 也是 `[]`。
 *
 * Splits into lines at the server's separators, each line keeping its own terminator;
 * a trailing terminator produces no extra empty line.
 *
 * An empty string yields an empty array, matching the server: `''.splitlines()` is
 * `[]` too.
 */
export function splitLines(text: string): string[] {
  if (!text) return [];

  const lines: string[] = [];
  let start = 0;
  for (let index = 0; index < text.length; index++) {
    if (!LINE_BREAK_CODES.has(text.charCodeAt(index))) continue;
    // `\r\n` 是一个分隔符：把紧随的 `\n` 并入同一行，别切成两行。
    // `\r\n` is one separator: fold the following `\n` into the same line rather than
    // splitting twice.
    const paired =
      text.charCodeAt(index) === 0x0d && text.charCodeAt(index + 1) === 0x0a;
    const end = index + (paired ? 2 : 1);
    lines.push(text.slice(start, end));
    start = end;
    index = end - 1;
  }
  if (start < text.length) lines.push(text.slice(start));
  return lines;
}

/**
 * 把行拼回文本。行自带换行符，直接相连即可，不额外补。
 * Joins lines back into text. Each line carries its own terminator, so they simply
 * concatenate without adding any.
 */
export function joinLines(lines: readonly string[]): string {
  return lines.join('');
}
