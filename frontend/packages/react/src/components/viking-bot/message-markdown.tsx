import { useMemo } from 'react';
import { MDRenderer } from '@ark-design/ui/markdown-renderer';
import type { MDRendererProps } from '@ark-design/ui/markdown-renderer';
import { MessageCodeBlock } from './message-code-block.js';
import {
  resourceAstPlugin,
  resourceHref,
  safeMarkdownHref,
} from './markdown-links.js';
import type { VikingBotLabels } from './model.js';

const AST_PLUGINS = [resourceAstPlugin];

/** Shared Ark renderer for answers, retaining the reference version's link slots. */
export function MessageMarkdown({
  text,
  streaming,
  user,
  labels,
  onOpenUri,
}: {
  text: string;
  streaming?: boolean;
  user?: boolean;
  labels: VikingBotLabels;
  onOpenUri?: (uri: string) => void;
}) {
  const { copy, copied } = labels;
  const slots = useMemo<NonNullable<MDRendererProps['slots']>>(
    () => ({
      CodeBlock: ({ code, language }) => (
        <MessageCodeBlock
          code={code}
          language={language}
          labels={{ copy, copied }}
          onOpenUri={onOpenUri}
        />
      ),
      Link: ({ children, href, title }) => {
        const uri = resourceHref(href);
        const safeHref = uri ?? safeMarkdownHref(href);
        if (!safeHref) return <>{children}</>;
        return (
          <a
            href={safeHref}
            title={title}
            target={!uri && /^https?:/i.test(href ?? '') ? '_blank' : undefined}
            rel="noopener noreferrer"
            onClick={
              uri
                ? (event) => {
                    event.preventDefault();
                    onOpenUri?.(uri);
                  }
                : undefined
            }
          >
            {children}
          </a>
        );
      },
    }),
    [onOpenUri, copy, copied],
  );
  return (
    <MDRenderer
      className="ov-viking-bot__text ov-viking-bot__text--markdown"
      astPlugins={user ? undefined : AST_PLUGINS}
      parsing={user ? { breaks: true, enabledHtmlTags: false } : undefined}
      markdown={text}
      showIndicator={streaming}
      style={
        user ? { fontSize: 'inherit', overflowWrap: 'anywhere' } : undefined
      }
      slots={user ? undefined : slots}
    />
  );
}
