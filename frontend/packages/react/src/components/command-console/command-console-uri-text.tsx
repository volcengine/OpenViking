import type { ReactNode } from 'react';

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
  /** Every caller declares its content semantics; literal values retain filename punctuation. */
  mode: 'prose' | 'literal';
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

/** 把纯文本中的 URI 渲染成链接；宿主可通过回调接管打开。 */
export function CommandConsoleUriText({
  text,
  onOpenUri,
  editor = false,
  mode,
}: CommandConsoleUriTextProps) {
  const parts: ReactNode[] = [];
  let cursor = 0;
  const pattern = new RegExp(
    (mode === 'prose'
      ? COMMAND_CONSOLE_PROSE_URI_PATTERN
      : COMMAND_CONSOLE_URI_PATTERN
    ).source,
    'gi',
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
    const index = matched.index ?? 0;
    const uri =
      mode === 'prose'
        ? matched[0].replace(/[.!?；;:：！？]+$/u, '')
        : matched[0];
    if (index > cursor) parts.push(plain(text.slice(cursor, index), cursor));
    parts.push(
      <CommandConsoleUriLink
        key={`${index}-${uri}`}
        uri={uri}
        onOpenUri={onOpenUri}
        editor={editor}
      >
        {uri}
      </CommandConsoleUriLink>,
    );
    cursor = index + uri.length;
  }

  if (!parts.length) return <>{plain(text, 0)}</>;
  if (cursor < text.length) parts.push(plain(text.slice(cursor), cursor));
  return <>{parts}</>;
}
