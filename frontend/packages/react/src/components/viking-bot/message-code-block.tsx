import { useMemo } from 'react';
import { BubbleTools } from '@ark-design/ui/bubble';
import { MDRenderer } from '@ark-design/ui/markdown-renderer';
import type { MDRendererProps } from '@ark-design/ui/markdown-renderer';
import { resourceText } from './markdown-links.js';
import type { VikingBotLabels } from './model.js';

/** Ark owns highlighting and clipboard feedback; only the host's resource links are added. */
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
  const { copy, copied } = labels;
  const slots = useMemo<NonNullable<MDRendererProps['slots']>>(
    () => ({
      CodeBlockHeader: () => (
        <BubbleTools
          className="ov-viking-bot__code-copy"
          items={['copy']}
          copyText={code}
          labels={{ copy, copied }}
        />
      ),
      ...(/^(bash|shell|sh|text|txt)$/i.test(language) &&
      onOpenUri &&
      resourceText(code, false).length
        ? {
            CodeBlockHighlighter: () => {
              let cursor = 0;
              const nodes = resourceText(code, false).flatMap((match) => {
                const before = code.slice(cursor, match.start);
                cursor = match.end;
                return [
                  before,
                  <a
                    key={match.start}
                    href={match.uri}
                    onClick={(event) => {
                      event.preventDefault();
                      onOpenUri(match.uri);
                    }}
                  >
                    {match.uri}
                  </a>,
                ];
              });
              return (
                <pre>
                  <code>
                    {nodes}
                    {code.slice(cursor)}
                  </code>
                </pre>
              );
            },
          }
        : {}),
    }),
    [code, language, onOpenUri, copy, copied],
  );
  const fence = '`'.repeat(
    Math.max(
      3,
      ...Array.from(code.matchAll(/`+/g), (match) => match[0].length + 1),
    ),
  );
  return (
    <div
      className={`ov-viking-bot__code${language === 'text' ? ' ov-viking-bot__code--wrap' : ''}`}
    >
      <MDRenderer
        markdown={`${fence}${language.replace(/[^\w+-]/g, '')}\n${code}\n${fence}`}
        slots={slots}
      />
    </div>
  );
}
