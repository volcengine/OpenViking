/** Display leading YAML metadata as code without changing the stored document. */
export function splitMarkdownFrontmatter(
  markdown: string,
): { rawFrontmatter: string; body: string } | null {
  const match =
    /^(?:\uFEFF)?---[ \t]*\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/.exec(
      markdown,
    )
  if (!match) return null
  return {
    rawFrontmatter: match[0].replace(/^\uFEFF/, '').trimEnd(),
    body: markdown.slice(match[0].length),
  }
}
