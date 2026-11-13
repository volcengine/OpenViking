/** A safe relative token survives Markdown's HTML sanitizer without allowing new protocols. */
const PREFIX = './__ov_viking_image__/';
export function imagePreviewTarget(url: string): string {
  return /^viking:\/\//i.test(url) ? PREFIX + encodeURIComponent(url) : url;
}
export function originalImageTarget(url: string): string {
  if (!url.startsWith(PREFIX)) return url;
  try {
    const original = decodeURIComponent(url.slice(PREFIX.length));
    return /^viking:\/\//i.test(original) ? original : url;
  } catch {
    return url;
  }
}
type Node = {
  type: string;
  value?: string;
  url?: string;
  identifier?: string;
  alt?: string | null;
  title?: string | null;
  children?: Node[];
};

const importedImageLine =
  /^ {0,3}!\[([^\]\\\r\n]*)\]\((viking:\/\/[^\s<>()]+)\)[ \t]*$/i;

/**
 * Older imports put the extracted image directly after the KBImage closing
 * tags. CommonMark consumes that line as part of the HTML block. Recover it
 * from HTML nodes only, so examples in code spans and fences stay literal.
 */
export function recoverImportedMarkdownImages(root: Node): void {
  root.children = root.children?.flatMap((node) => {
    if (node.type !== 'html' || !node.value) return [node];
    const lines = node.value.split(/\r?\n/);
    const boundary = lines.findIndex((line) =>
      /^[ \t]*(?:<\/[a-z]+>[ \t]*)*<\/kbimage>[ \t]*$/i.test(line),
    );
    if (boundary === -1) return [node];
    const images = lines
      .slice(boundary + 1)
      .map((line) => importedImageLine.exec(line));
    if (!images.length || !images.every((match) => match !== null))
      return [node];

    return [
      { ...node, value: lines.slice(0, boundary + 1).join('\n') },
      ...images.map(([, alt, url]) => ({
        type: 'paragraph',
        children: [{ type: 'image', url, alt }],
      })),
    ];
  });
}

/** Rewrite image nodes only. Fenced code and normal links remain untouched. */
export function preserveVikingImages(root: Node): void {
  const definitions = new Map<string, Node>();
  function collect(node: Node) {
    if (
      node.type === 'definition' &&
      node.identifier &&
      !definitions.has(node.identifier.toUpperCase())
    ) {
      definitions.set(node.identifier.toUpperCase(), node);
    }
    node.children?.forEach(collect);
  }
  function visit(node: Node) {
    if (node.type === 'image' && node.url)
      node.url = imagePreviewTarget(node.url);
    if (node.type === 'imageReference' && node.identifier) {
      const definition = definitions.get(node.identifier.toUpperCase());
      if (definition?.url && /^viking:\/\//i.test(definition.url)) {
        node.type = 'image';
        node.url = imagePreviewTarget(definition.url);
        node.title = definition.title;
      }
    }
    node.children?.forEach(visit);
  }
  collect(root);
  visit(root);
}
