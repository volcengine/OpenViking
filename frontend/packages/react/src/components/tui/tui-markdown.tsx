import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { TuiUriText } from './tui-uri-text.js';

export interface TuiMarkdownProps {
  text: string;
  onOpenUri?: (uri: string) => void;
}

function textOf(children: ReactNode): string {
  if (typeof children === 'string' || typeof children === 'number') {
    return String(children);
  }
  if (Array.isArray(children)) return children.map(textOf).join('');
  return '';
}

/** Markdown 结果；代码块与行内代码保持等宽，URI 可打开。 */
export function TuiMarkdown({ text, onOpenUri }: TuiMarkdownProps) {
  return (
    <div className="ov-tui__markdown">
      <ReactMarkdown
        remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
        components={{
          p: ({ children }) => <p>{children}</p>,
          ul: ({ children }) => <ul>{children}</ul>,
          ol: ({ children }) => <ol>{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          code: ({ children }) => (
            <code>
              <TuiUriText text={textOf(children)} onOpenUri={onOpenUri} />
            </code>
          ),
          pre: ({ children }) => <pre>{children}</pre>,
          table: ({ children }) => (
            <div className="ov-tui__table-frame">
              <table>{children}</table>
            </div>
          ),
          a: ({ children, href, ...props }) => {
            const target = href ?? textOf(children);
            const isViking = target.startsWith('viking://');
            if (isViking) {
              return (
                <button
                  type="button"
                  className="ov-tui__link"
                  onClick={() => onOpenUri?.(target)}
                >
                  {children}
                </button>
              );
            }
            return (
              <a {...props} href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
