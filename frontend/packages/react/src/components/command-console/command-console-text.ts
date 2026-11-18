/** Literal/code tokens end at whitespace or quoting delimiters, not filename punctuation. */
export const COMMAND_CONSOLE_URI_PATTERN = /(?:viking|https?):\/\/[^\s"'<>`]+/g;
/** Prose retains sentence punctuation outside automatic links. */
export const COMMAND_CONSOLE_PROSE_URI_PATTERN =
  /(?:viking|https?):\/\/[^\s,，。；;、"'<>()[\]]+/g;

/** Resource targets permit only explicit resource or web navigation schemes. */
export function getCommandConsoleSafeHref(
  uri: string | undefined,
): string | undefined {
  const href = uri?.trim();
  return href && /^(?:viking|https?):\/\/[^\s]+$/i.test(href)
    ? href
    : undefined;
}

/** 复制文本到剪贴板；不可用时返回 `false`，不抛出。 */
export async function copyCommandConsoleText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    return false;
  }

  return false;
}

/** Validate JSON, then indent its original tokens without rewriting values. */
export function formatCommandConsoleJson(text: string): string {
  const trimmed = text.trim();
  if (!trimmed || !/^[[{]/.test(trimmed)) return text;

  try {
    JSON.parse(trimmed);
  } catch {
    return text;
  }
  const tokens = trimmed.match(/"(?:\\.|[^"\\])*"|[^\s]/g) ?? [];
  let depth = 0;
  let output = '';
  const newline = () => `\n${'  '.repeat(depth)}`;
  for (const [index, token] of tokens.entries()) {
    if (token === '{' || token === '[') {
      output += token;
      depth++;
      if (tokens[index + 1] !== '}' && tokens[index + 1] !== ']')
        output += newline();
    } else if (token === '}' || token === ']') {
      depth--;
      if (tokens[index - 1] !== '{' && tokens[index - 1] !== '[')
        output += newline();
      output += token;
    } else if (token === ',') output += `,${newline()}`;
    else if (token === ':') output += ': ';
    else output += token;
  }
  return output;
}
