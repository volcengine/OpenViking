/** 展示宿主命令前缀与动作，保留原始大小写和空白。 */
export function TuiCommandText({
  text,
  name,
  namespaceOnly = false,
}: {
  text: string;
  name?: string;
  namespaceOnly?: boolean;
}) {
  const namedStart = name ? text.length - name.length : -1;
  const matchesName =
    namedStart >= 0 &&
    text.slice(namedStart).toLowerCase() === name?.toLowerCase() &&
    (namedStart === 0 || /\s/.test(text[namedStart - 1]!));
  const lastToken = text.search(/\S+$/);
  const start = namespaceOnly
    ? text.length
    : matchesName
      ? namedStart
      : lastToken < 0
        ? text.length
        : lastToken;

  return (
    <>
      {start > 0 ? (
        <span className="ov-tui__command-prefix">{text.slice(0, start)}</span>
      ) : null}
      {start < text.length ? (
        <span className="ov-tui__command-name">{text.slice(start)}</span>
      ) : null}
    </>
  );
}
