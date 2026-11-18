import type { ReactNode } from 'react';
import type { CommandConsoleCommandInputMatch } from './command-input.js';

import {
  getCommandConsoleSafeHref,
  COMMAND_CONSOLE_URI_PATTERN,
  COMMAND_CONSOLE_PROSE_URI_PATTERN,
} from './command-console-text.js';

export interface CommandConsoleUriTextProps {
  text: string;
  onOpenUri?: (uri: string) => void;
  /** 编辑镜像隐藏重复文字，但保留链接语义及输入框焦点。 */
  editor?: boolean;
  mode: 'prose' | 'literal' | 'json';
  wholeUri?: boolean;
}

/** A validated native link; rejected targets remain plain text. */
export function CommandConsoleUriLink({
  uri,
  children,
  onOpenUri,
  editor = false,
  className = 'ov-command-console__link',
}: {
  uri?: string;
  children: ReactNode;
  onOpenUri?: (uri: string) => void;
  editor?: boolean;
  className?: string;
}) {
  const href = getCommandConsoleSafeHref(uri);
  if (!href) return <>{children}</>;
  return (
    <a
      href={href}
      className={className}
      onMouseDown={editor ? (event) => event.preventDefault() : undefined}
      onClick={
        onOpenUri
          ? (event) => {
              event.preventDefault();
              onOpenUri(href);
            }
          : undefined
      }
    >
      {children}
    </a>
  );
}

const DOUBLE_QUOTED_URI = String.raw`"(?<double>viking:\/\/(?:\\.|[^"\\\r\n])+)"`;
// The JSON formatter puts each object key on its own line.
const JSON_URI_FIELD =
  String.raw`^(?<field>[ \t]*"(?:[^"\\]*_)?uri":[ \t]*)` +
  DOUBLE_QUOTED_URI +
  '|';

/** 把纯文本中的 URI 渲染成链接；宿主可通过回调接管打开。 */
export function CommandConsoleUriText({
  text,
  onOpenUri,
  editor = false,
  mode,
  wholeUri,
}: CommandConsoleUriTextProps) {
  if (wholeUri && /^viking:\/\//i.test(text)) {
    return (
      <CommandConsoleUriLink uri={text} onOpenUri={onOpenUri}>
        {text}
      </CommandConsoleUriLink>
    );
  }
  const parts: ReactNode[] = [];
  let cursor = 0;
  const quotedPattern = mode === 'json' ? JSON_URI_FIELD : '';
  const pattern = new RegExp(
    quotedPattern +
      (mode === 'prose'
        ? COMMAND_CONSOLE_PROSE_URI_PATTERN
        : COMMAND_CONSOLE_URI_PATTERN
      ).source,
    'gim',
  );
  const plain = (value: string, key: number) =>
    editor ? (
      <span key={key} aria-hidden="true">
        {value}
      </span>
    ) : (
      value
    );

  for (const matched of text.matchAll(pattern)) {
    const quoted = matched.groups?.double;
    const index =
      matched.index +
      (quoted === undefined ? 0 : (matched.groups?.field?.length ?? 0) + 1);
    const display =
      quoted ??
      (mode === 'prose'
        ? matched[0].replace(/[.!?；;:：！？]+$/u, '')
        : matched[0]);
    const uri =
      quoted === undefined ? display : (JSON.parse(`"${quoted}"`) as string);
    if (index > cursor) parts.push(plain(text.slice(cursor, index), cursor));
    parts.push(
      <CommandConsoleUriLink
        key={`${index}-${uri}`}
        uri={uri}
        onOpenUri={onOpenUri}
        editor={editor}
      >
        {display}
      </CommandConsoleUriLink>,
    );
    cursor = index + display.length;
  }

  if (!parts.length) return <>{plain(text, 0)}</>;
  if (cursor < text.length) parts.push(plain(text.slice(cursor), cursor));
  return <>{parts}</>;
}

export function CommandConsoleCommandArguments({
  input,
  editor,
  onOpenUri,
}: {
  input: CommandConsoleCommandInputMatch;
  editor?: boolean;
  onOpenUri?: (uri: string) => void;
}) {
  const { rest, command } = input;
  const ranges = command.resolveUriArguments?.(rest) ?? [];
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const { start, end, uri } of ranges) {
    parts.push(
      <CommandConsoleUriText
        key={cursor}
        text={rest.slice(cursor, start)}
        mode="literal"
        editor={editor}
        onOpenUri={onOpenUri}
      />,
      <CommandConsoleUriLink
        key={`uri-${start}`}
        uri={uri}
        editor={editor}
        onOpenUri={onOpenUri}
      >
        {rest.slice(start, end)}
      </CommandConsoleUriLink>,
    );
    cursor = end;
  }
  return (
    <>
      {parts}
      <CommandConsoleUriText
        text={rest.slice(cursor)}
        mode="literal"
        editor={editor}
        onOpenUri={onOpenUri}
      />
    </>
  );
}
