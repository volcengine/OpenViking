import { Children, isValidElement, type ReactNode } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { TuiUriText } from './tui-uri-text.js';
import { TuiCodeBlock } from './tui-code-block.js';
import type { ResolvedTuiLabels } from './tui-labels.js';

export interface TuiMarkdownProps {
  text: string;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  labels: ResolvedTuiLabels;
}

function textOf(children: ReactNode): string {
  if (typeof children === 'string' || typeof children === 'number') {
    return String(children);
  }
  if (Array.isArray(children)) return children.map(textOf).join('');
  if (isValidElement<{ children?: ReactNode }>(children))
    return textOf(children.props.children);
  return '';
}

/** Markdown 结果；代码块与行内代码保持等宽，URI 可打开。 */
export function TuiMarkdown({
  text,
  onOpenUri,
  onCopy,
  labels,
}: TuiMarkdownProps) {
  const linked = (children: ReactNode) =>
    Children.map(children, (child) =>
      typeof child === 'string' ? (
        <TuiUriText text={child} onOpenUri={onOpenUri} />
      ) : (
        child
      ),
    );
  return (
    <div className="ov-tui__markdown">
      <ReactMarkdown
        remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
        urlTransform={(url) =>
          /^viking:\/\//i.test(url) ? url : defaultUrlTransform(url)
        }
        components={{
          p: ({ children }) => <p>{linked(children)}</p>,
          ul: ({ children }) => <ul>{children}</ul>,
          ol: ({ children }) => <ol>{children}</ol>,
          li: ({ children }) => <li>{linked(children)}</li>,
          h1: ({ children }) => <h1>{linked(children)}</h1>,
          h2: ({ children }) => <h2>{linked(children)}</h2>,
          h3: ({ children }) => <h3>{linked(children)}</h3>,
          h4: ({ children }) => <h4>{linked(children)}</h4>,
          h5: ({ children }) => <h5>{linked(children)}</h5>,
          h6: ({ children }) => <h6>{linked(children)}</h6>,
          strong: ({ children }) => <strong>{linked(children)}</strong>,
          em: ({ children }) => <em>{linked(children)}</em>,
          del: ({ children }) => <del>{linked(children)}</del>,
          td: ({ children, style }) => (
            <td style={style}>{linked(children)}</td>
          ),
          th: ({ children, style }) => (
            <th style={style}>{linked(children)}</th>
          ),
          code: ({ children }) => (
            <code>
              <TuiUriText text={textOf(children)} onOpenUri={onOpenUri} />
            </code>
          ),
          pre: ({ children }) => (
            <TuiCodeBlock
              text={textOf(children).replace(/\n$/, '')}
              labels={labels}
              onCopy={onCopy}
              onOpenUri={onOpenUri}
            />
          ),
          table: ({ children }) => (
            <div className="ov-tui__table-frame">
              <table>{children}</table>
            </div>
          ),
          a: ({ children, href, title }) => {
            const target = href ?? textOf(children);
            const isViking = /^viking:\/\//i.test(target);
            if (isViking) {
              return (
                <a
                  href={target}
                  title={title}
                  className="ov-tui__link"
                  onClick={
                    onOpenUri
                      ? (event) => {
                          event.preventDefault();
                          onOpenUri(target);
                        }
                      : undefined
                  }
                >
                  {children}
                </a>
              );
            }
            return (
              <a href={href} title={title} target="_blank" rel="noreferrer">
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
