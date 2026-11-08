import type { ReactNode } from 'react';

import { TUI_URI_PATTERN } from './tui-text.js';

export interface TuiUriTextProps {
  text: string;
  onOpenUri?: (uri: string) => void;
}

function renderUri(
  uri: string,
  key: string,
  onOpenUri?: (uri: string) => void,
) {
  if (!onOpenUri) {
    return <span key={key}>{uri}</span>;
  }

  return (
    <button
      key={key}
      type="button"
      className="ov-tui__link"
      onClick={() => onOpenUri(uri)}
    >
      {uri}
    </button>
  );
}

/** 把纯文本中的 URI 渲染成可打开入口。Turns URIs in plain text into links. */
export function TuiUriText({ text, onOpenUri }: TuiUriTextProps) {
  const parts: ReactNode[] = [];
  let cursor = 0;
  const pattern = new RegExp(TUI_URI_PATTERN.source, 'g');

  for (const matched of text.matchAll(pattern)) {
    const index = matched.index ?? 0;
    const value = matched[0];
    if (index > cursor) parts.push(text.slice(cursor, index));
    parts.push(renderUri(value, `${index}-${value}`, onOpenUri));
    cursor = index + value.length;
  }

  if (!parts.length) return <>{text}</>;
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}
