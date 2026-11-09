import type { TuiCommandDefinition } from './command-catalog.js';
import { matchTuiCommandInput } from './command-input.js';

/** 命令已出现在卡片头时，不重复显示同名正文标题。 */
export function getVisibleTuiResultTitle(
  title: string | undefined,
  command?: TuiCommandDefinition,
): string | undefined {
  if (!title || !command) return title;
  const candidate = title
    .trim()
    .replace(/^\*\*([\s\S]*)\*\*$/, '$1')
    .replace(/^\/+/, '')
    .trim();
  const match = matchTuiCommandInput(candidate, [command]);
  return match && !match.rest.trim() ? undefined : title;
}

export function getTuiResultText(
  text: string,
  command?: TuiCommandDefinition,
): string {
  const heading = text.match(
    /^\s*(\*\*\/?[^\r\n]+\*\*|\/[^\r\n]+)\r?\n(?:[ \t]*\r?\n)?/,
  );
  return heading && getVisibleTuiResultTitle(heading[1], command) === undefined
    ? text.slice(heading[0].length)
    : text;
}

export function isTuiJson(text: string): boolean {
  const first = text.trimStart().charAt(0);
  if (first !== '{' && first !== '[') return false;
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
}
