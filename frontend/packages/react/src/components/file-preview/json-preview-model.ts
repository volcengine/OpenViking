export interface JsonPreviewLine {
  closeLine?: number;
  collapsedText?: string;
  depth: number;
  lineNumber: number;
  parentOpen?: number;
  text: string;
}

export interface JsonPreviewModel {
  defaultCollapsed: ReadonlySet<number>;
  lines: readonly JsonPreviewLine[];
  text: string;
}

export const MAX_FORMATTED_JSON_PREVIEW_LENGTH = 1_000_000;

/** Format JSON without parsing numbers back through JavaScript and losing source tokens. */
export function formatJsonPreviewContent(content: string) {
  if (!content.trim()) return '';
  try {
    JSON.parse(content);
  } catch {
    return undefined;
  }

  let formatted = '';
  let depth = 0;
  let inString = false;
  let escaped = false;
  const indent = () => '  '.repeat(depth);
  const append = (value: string) => {
    if (formatted.length + value.length > MAX_FORMATTED_JSON_PREVIEW_LENGTH)
      return false;
    formatted += value;
    return true;
  };

  for (let index = 0; index < content.length; index += 1) {
    const character = content[index]!;
    if (inString) {
      if (!append(character)) return content;
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') {
      inString = true;
      if (!append(character)) return content;
      continue;
    }
    if (/\s/.test(character)) continue;
    if (character === '{' || character === '[') {
      let next = index + 1;
      while (next < content.length && /\s/.test(content[next]!)) next += 1;
      const closing = character === '{' ? '}' : ']';
      if (content[next] === closing) {
        if (!append(`${character}${closing}`)) return content;
        index = next;
      } else {
        depth += 1;
        if (!append(`${character}\n${indent()}`)) return content;
      }
      continue;
    }
    if (character === '}' || character === ']') {
      depth -= 1;
      if (!append(`\n${indent()}${character}`)) return content;
      continue;
    }
    if (character === ',') {
      if (!append(`,\n${indent()}`)) return content;
      continue;
    }
    if (!append(character === ':' ? ': ' : character)) return content;
  }
  return formatted;
}

export function createJsonPreviewModel(
  content: string,
  defaultExpandDepth = 4,
): JsonPreviewModel | undefined {
  const text = formatJsonPreviewContent(content);
  if (text === undefined) return undefined;
  const lines: JsonPreviewLine[] = [];
  const openLines: number[] = [];
  const defaultCollapsed = new Set<number>();

  for (const [index, value] of text.split('\n').entries()) {
    const trimmed = value.trim();
    const closes = /^[}\]](?:,)?$/.test(trimmed);
    const parentOpen = openLines.at(-1);
    const line: JsonPreviewLine = {
      depth: Math.floor((value.length - value.trimStart().length) / 2),
      lineNumber: index + 1,
      parentOpen,
      text: value,
    };
    lines.push(line);

    if (closes) {
      const opening = openLines.pop();
      if (opening !== undefined) {
        const openLine = lines[opening]!;
        openLine.closeLine = index;
        openLine.collapsedText = `${openLine.text} … ${trimmed}`;
      }
      continue;
    }
    if (/[{[]$/.test(trimmed)) {
      if (line.depth >= defaultExpandDepth) defaultCollapsed.add(index);
      openLines.push(index);
    }
  }

  return { defaultCollapsed, lines, text };
}

export function visibleJsonPreviewLines(
  model: JsonPreviewModel,
  collapsed: ReadonlySet<number>,
) {
  return model.lines.filter((line) => {
    let ancestor = line.parentOpen;
    while (ancestor !== undefined) {
      if (collapsed.has(ancestor)) return false;
      ancestor = model.lines[ancestor]?.parentOpen;
    }
    return true;
  });
}
