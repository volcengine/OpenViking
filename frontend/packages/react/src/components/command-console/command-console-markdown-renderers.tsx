import {
  Children,
  createContext,
  isValidElement,
  useContext,
  type ReactNode,
  type ComponentPropsWithoutRef,
} from 'react';
import type { Components } from 'react-markdown';
import { CommandConsoleUriText } from './command-console-uri-text.js';
import { getCommandConsoleSafeHref } from './command-console-text.js';
import { CommandConsoleCodeBlock } from './command-console-code-block.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';

export interface CommandConsoleMarkdownOptions {
  labels: ResolvedCommandConsoleLabels;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
}
export const CommandConsoleMarkdownContext =
  createContext<CommandConsoleMarkdownOptions | null>(null);
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
  const options = useContext(CommandConsoleMarkdownContext)!;
  const insideLink = useContext(InsideLink);
  return (
    <>
      {insideLink
        ? children
        : Children.map(children, (child) =>
            typeof child === 'string' ? (
              <CommandConsoleUriText
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
  const options = useContext(CommandConsoleMarkdownContext)!;
  const insideLink = useContext(InsideLink);
  return (
    <code>
      {insideLink ? (
        children
      ) : (
        <CommandConsoleUriText
          text={textOf(children)}
          mode="literal"
          onOpenUri={options.onOpenUri}
        />
      )}
    </code>
  );
}

function MarkdownPre({ children }: ComponentPropsWithoutRef<'pre'>) {
  const options = useContext(CommandConsoleMarkdownContext)!;
  return (
    <CommandConsoleCodeBlock
      text={textOf(children).replace(/\n$/, '')}
      {...options}
    />
  );
}

function MarkdownLink({
  children,
  href,
  title,
}: ComponentPropsWithoutRef<'a'>) {
  const { onOpenUri } = useContext(CommandConsoleMarkdownContext)!;
  const isViking = /^viking:/i.test(href ?? '');
  const target = isViking ? getCommandConsoleSafeHref(href) : href;
  return (
    <InsideLink.Provider value={true}>
      {target ? (
        <a
          href={target}
          title={title}
          className={isViking ? 'ov-command-console__link' : undefined}
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
export const COMMAND_CONSOLE_MARKDOWN_COMPONENTS: Components = {
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
    <div className="ov-command-console__table-frame">
      <table>{children}</table>
    </div>
  ),
};
