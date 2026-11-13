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
  url?: string;
  identifier?: string;
  alt?: string | null;
  title?: string | null;
  children?: Node[];
};
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
