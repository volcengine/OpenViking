import {
  Children,
  createContext,
  isValidElement,
  useContext,
  type ReactNode,
  type ComponentPropsWithoutRef,
} from 'react';
import type { Components } from 'react-markdown';
import { TuiUriText } from './tui-uri-text.js';
import { getTuiSafeHref } from './tui-text.js';
import { TuiCodeBlock } from './tui-code-block.js';
import type { ResolvedTuiLabels } from './tui-labels.js';

export interface TuiMarkdownOptions {
  labels: ResolvedTuiLabels;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
}
export const TuiMarkdownContext = createContext<TuiMarkdownOptions | null>(
  null,
);
const InsideLink = createContext(false);

function textOf(children: ReactNode): string {
  if (typeof children === 'string' || typeof children === 'number')
    return String(children);
  if (Array.isArray(children)) return children.map(textOf).join('');
  if (isValidElement<{ children?: ReactNode }>(children))
    return textOf(children.props.children);
  return '';
}

function LinkedText({ children }: { children?: ReactNode }) {
  const options = useContext(TuiMarkdownContext)!;
  const insideLink = useContext(InsideLink);
  return (
    <>
      {insideLink
        ? children
        : Children.map(children, (child) =>
            typeof child === 'string' ? (
              <TuiUriText
                text={child}
                mode="prose"
                onOpenUri={options.onOpenUri}
              />
            ) : (
              child
            ),
          )}
    </>
  );
}

function MarkdownCode({ children }: ComponentPropsWithoutRef<'code'>) {
  const options = useContext(TuiMarkdownContext)!;
  const insideLink = useContext(InsideLink);
  return (
    <code>
      {insideLink ? (
        children
      ) : (
        <TuiUriText
          text={textOf(children)}
          mode="literal"
          onOpenUri={options.onOpenUri}
        />
      )}
    </code>
  );
}

function MarkdownPre({ children }: ComponentPropsWithoutRef<'pre'>) {
  const options = useContext(TuiMarkdownContext)!;
  return (
    <TuiCodeBlock text={textOf(children).replace(/\n$/, '')} {...options} />
  );
}

function MarkdownLink({
  children,
  href,
  title,
}: ComponentPropsWithoutRef<'a'>) {
  const { onOpenUri } = useContext(TuiMarkdownContext)!;
  const isViking = /^viking:/i.test(href ?? '');
  const target = isViking ? getTuiSafeHref(href) : href;
  return (
    <InsideLink.Provider value={true}>
      {target ? (
        <a
          href={target}
          title={title}
          className={isViking ? 'ov-tui__link' : undefined}
          target={isViking ? undefined : '_blank'}
          rel={isViking ? undefined : 'noreferrer'}
          onClick={
            isViking && onOpenUri
              ? (event) => {
                  event.preventDefault();
                  onOpenUri(target);
                }
              : undefined
          }
        >
          {children}
        </a>
      ) : (
        children
      )}
    </InsideLink.Provider>
  );
}

/** Stable component types preserve DOM identity, focus and copy feedback across input updates. */
export const TUI_MARKDOWN_COMPONENTS: Components = {
  p: ({ children }) => (
    <p>
      <LinkedText>{children}</LinkedText>
    </p>
  ),
  li: ({ children }) => (
    <li>
      <LinkedText>{children}</LinkedText>
    </li>
  ),
  h1: ({ children }) => (
    <h1>
      <LinkedText>{children}</LinkedText>
    </h1>
  ),
  h2: ({ children }) => (
    <h2>
      <LinkedText>{children}</LinkedText>
    </h2>
  ),
  h3: ({ children }) => (
    <h3>
      <LinkedText>{children}</LinkedText>
    </h3>
  ),
  h4: ({ children }) => (
    <h4>
      <LinkedText>{children}</LinkedText>
    </h4>
  ),
  h5: ({ children }) => (
    <h5>
      <LinkedText>{children}</LinkedText>
    </h5>
  ),
  h6: ({ children }) => (
    <h6>
      <LinkedText>{children}</LinkedText>
    </h6>
  ),
  strong: ({ children }) => (
    <strong>
      <LinkedText>{children}</LinkedText>
    </strong>
  ),
  em: ({ children }) => (
    <em>
      <LinkedText>{children}</LinkedText>
    </em>
  ),
  del: ({ children }) => (
    <del>
      <LinkedText>{children}</LinkedText>
    </del>
  ),
  td: ({ children, style }) => (
    <td style={style}>
      <LinkedText>{children}</LinkedText>
    </td>
  ),
  th: ({ children, style }) => (
    <th style={style}>
      <LinkedText>{children}</LinkedText>
    </th>
  ),
  code: MarkdownCode,
  pre: MarkdownPre,
  a: MarkdownLink,
  table: ({ children }) => (
    <div className="ov-tui__table-frame">
      <table>{children}</table>
    </div>
  ),
};
