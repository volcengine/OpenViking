import type { ReactNode } from 'react';

import {
  getTuiSafeHref,
  TUI_URI_PATTERN,
  TUI_PROSE_URI_PATTERN,
} from './tui-text.js';

export interface TuiUriTextProps {
  text: string;
  onOpenUri?: (uri: string) => void;
  /** 编辑镜像隐藏重复文字，但保留链接语义及输入框焦点。 */
  editor?: boolean;
  /** Every caller declares its content semantics; literal values retain filename punctuation. */
  mode: 'prose' | 'literal';
}

/** A validated native link; rejected targets remain plain text. */
export function TuiUriLink({
  uri,
  children,
  onOpenUri,
  editor = false,
  className = 'ov-tui__link',
}: {
  uri?: string;
  children: ReactNode;
  onOpenUri?: (uri: string) => void;
  editor?: boolean;
  className?: string;
}) {
  const href = getTuiSafeHref(uri);
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
export function TuiUriText({
  text,
  onOpenUri,
  editor = false,
  mode,
}: TuiUriTextProps) {
  const parts: ReactNode[] = [];
  let cursor = 0;
  const pattern = new RegExp(
    (mode === 'prose' ? TUI_PROSE_URI_PATTERN : TUI_URI_PATTERN).source,
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
      <TuiUriLink
        key={`${index}-${uri}`}
        uri={uri}
        onOpenUri={onOpenUri}
        editor={editor}
      >
        {uri}
      </TuiUriLink>,
    );
    cursor = index + uri.length;
  }

  if (!parts.length) return <>{plain(text, 0)}</>;
  if (cursor < text.length) parts.push(plain(text.slice(cursor), cursor));
  return <>{parts}</>;
}
