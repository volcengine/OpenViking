import { CopyAction } from './copy-action.js';
import { useCodeTokens } from './code-tokens.js';
import { resourceText } from './markdown-links.js';
import type { VikingBotLabels } from './model.js';

export function MessageCodeBlock({
  code,
  language = 'text',
  labels,
  onOpenUri,
}: {
  code: string;
  language?: string;
  labels: VikingBotLabels;
  onOpenUri?: (uri: string) => void;
}) {
  const matches =
    onOpenUri && /^(bash|shell|sh|text|txt)$/i.test(language)
      ? resourceText(code, false)
      : [];
  const tokens = useCodeTokens(code, language, matches.length === 0);
  let cursor = 0;
  const links = matches.flatMap((match) => {
    const before = code.slice(cursor, match.start);
    cursor = match.end;
    return [
      before,
      <a
        key={match.start}
        href={match.uri}
        onClick={(event) => {
          event.preventDefault();
          onOpenUri?.(match.uri);
        }}
      >
        {match.uri}
      </a>,
    ];
  });
  return (
    <div
      className={`ov-viking-bot__code${language === 'text' ? ' ov-viking-bot__code--wrap' : ''}`}
    >
      <CopyAction
        className="ov-viking-bot__code-copy"
        text={code}
        labels={labels}
      />
      <pre>
        <code>{links.length ? [...links, code.slice(cursor)] : tokens}</code>
      </pre>
    </div>
  );
}
