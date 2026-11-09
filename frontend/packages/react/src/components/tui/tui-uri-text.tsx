import type { ReactNode } from 'react';

import { TUI_URI_PATTERN } from './tui-text.js';

export interface TuiUriTextProps {
  text: string;
  onOpenUri?: (uri: string) => void;
  /** 编辑镜像隐藏重复文字，但保留链接语义及输入框焦点。 */
  editor?: boolean;
}

/** 把纯文本中的 URI 渲染成链接；宿主可通过回调接管打开。 */
export function TuiUriText({
  text,
  onOpenUri,
  editor = false,
}: TuiUriTextProps) {
  const parts: ReactNode[] = [];
  let cursor = 0;
  const pattern = new RegExp(TUI_URI_PATTERN.source, 'gi');
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
    const uri = matched[0];
    if (index > cursor) parts.push(plain(text.slice(cursor, index), cursor));
    parts.push(
      <a
        key={`${index}-${uri}`}
        href={uri}
        className="ov-tui__link"
        onMouseDown={editor ? (event) => event.preventDefault() : undefined}
        onClick={
          onOpenUri
            ? (event) => {
                event.preventDefault();
                onOpenUri(uri);
              }
            : undefined
        }
      >
        {uri}
      </a>,
    );
    cursor = index + uri.length;
  }

  if (!parts.length) return <>{plain(text, 0)}</>;
  if (cursor < text.length) parts.push(plain(text.slice(cursor), cursor));
  return <>{parts}</>;
}
