/** URI 匹配：`viking://` 开头，直到空白或常见标点。 */
export const TUI_URI_PATTERN = /viking:\/\/[^\s,，。；;、"'<>()[\]]+/g;

/** 复制文本到剪贴板；不可用时返回 `false`，不抛出。 */
export async function copyTuiText(text: string): Promise<boolean> {
  if (typeof navigator === 'undefined') return false;

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

/** 把 JSON 文本格式化为缩进输出；解析失败时原样返回。 */
export function formatTuiJson(text: string): string {
  const trimmed = text.trim();
  if (!trimmed || !/^[[{]/.test(trimmed)) return text;

  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return text;
  }
}
