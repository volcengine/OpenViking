interface MarkdownNode {
  type?: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

const PREFIX = '#viking-uri=';
const RESOURCE = /viking:\/\/[^\s<>"'`]+/gi;
const PROSE_RESOURCE = /viking:\/\/[^\s<>"'`，。；！？)\]]+/gi;
const SKIP = new Set(['link', 'linkReference', 'definition', 'code', 'html']);

/** Preserve normal Markdown navigation while rejecting executable URL schemes. */
export function safeMarkdownHref(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    return ['http:', 'https:', 'mailto:', 'tel:'].includes(
      new URL(value, 'https://openviking.invalid').protocol,
    )
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}

/** Only resource URIs enter the host callback; other schemes stay with Markdown. */
export function resourceHref(value?: string): string | undefined {
  if (value?.startsWith(PREFIX)) {
    try {
      value = decodeURIComponent(value.slice(PREFIX.length));
    } catch {
      return undefined;
    }
  }
  return value && /^viking:\/\/[^\s<>"'`]+$/i.test(value) ? value : undefined;
}

export function resourceText(value: string, prose = true) {
  const matches: { start: number; end: number; uri: string }[] = [];
  for (const match of value.matchAll(
    new RegExp(prose ? PROSE_RESOURCE : RESOURCE),
  )) {
    const uri = prose ? match[0].replace(/[.!?;:]+$/u, '') : match[0];
    matches.push({ start: match.index, end: match.index + uri.length, uri });
  }
  return matches;
}

const link = (uri: string, child: MarkdownNode): MarkdownNode => ({
  type: 'link',
  url: PREFIX + encodeURIComponent(uri),
  children: [child],
});

/** Transform AST nodes rather than synthesizing HTML from response text. */
export function linkResourceNodes(node: MarkdownNode): MarkdownNode {
  if ((node.type === 'link' || node.type === 'definition') && node.url) {
    const uri = resourceHref(node.url);
    if (uri) node.url = PREFIX + encodeURIComponent(uri);
  }
  if (!node.children || SKIP.has(node.type ?? '')) return node;
  node.children = node.children.flatMap((child) => {
    if (child.type === 'inlineCode') {
      const uri = resourceHref(child.value);
      return uri ? [link(uri, child)] : [child];
    }
    if (child.type !== 'text' || !child.value)
      return [linkResourceNodes(child)];
    const pieces: MarkdownNode[] = [];
    let cursor = 0;
    for (const match of resourceText(child.value)) {
      if (match.start > cursor)
        pieces.push({
          type: 'text',
          value: child.value.slice(cursor, match.start),
        });
      pieces.push(link(match.uri, { type: 'text', value: match.uri }));
      cursor = match.end;
    }
    if (!pieces.length) return [child];
    if (cursor < child.value.length)
      pieces.push({ type: 'text', value: child.value.slice(cursor) });
    return pieces;
  });
  return node;
}

export const resourceAstPlugin = {
  name: 'openviking-viking-uri-linkify',
  config: {},
  before: () => {},
  after: () => {},
  beforeEach: () => {},
  afterEach: () => {},
  modifier: (ast: unknown) => {
    if (ast && typeof ast === 'object' && 'type' in ast)
      linkResourceNodes(ast as MarkdownNode);
  },
};
