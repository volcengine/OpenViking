/**
 * Renders a translated sentence with its `backtick` spans as inline code, so
 * copy can name paths, headers and config keys without markup.
 */
export function CodeText({ text }: { text: string }) {
  return (
    <>
      {text.split('`').map((part, index) =>
        index % 2 === 1 ? (
          <code
            key={index}
            className="rounded border bg-muted/40 px-1 py-px font-mono text-[0.85em] break-all text-foreground"
          >
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </>
  )
}
