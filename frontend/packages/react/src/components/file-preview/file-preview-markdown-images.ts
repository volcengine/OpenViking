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
const importedImageBoundary = /^[ \t]*(?:<\/[a-z]+>[ \t]*)*<\/kbimage>[ \t]*$/i;

function escapeHtmlAttribute(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

/**
 * Older imports put the extracted image directly after the KBImage closing
 * tags. CommonMark consumes that line as HTML. Recover each image run in
 * place so the HTML parser still handles surrounding markup and comments,
 * and the resulting image still passes through the sanitizer.
 */
export function recoverImportedMarkdownImages(root: Node): void {
  for (const node of root.children ?? []) {
    if (node.type !== 'html' || !node.value) continue;
    let afterMarker = false;
    const lines = node.value.split(/(\r?\n)/);
    for (let index = 0; index < lines.length; index += 2) {
      const line = lines[index]!;
      const image = afterMarker ? importedImageLine.exec(line) : null;
      if (image) {
        const [, alt, url] = image;
        lines[index] =
          `<img alt="${escapeHtmlAttribute(alt!)}" src="${imagePreviewTarget(url!)}">`;
      } else {
        afterMarker = importedImageBoundary.test(line);
      }
    }
    node.value = lines.join('');
  }
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
