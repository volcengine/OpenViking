import type { TuiResourcePreviewContent } from './types.js';
import { TuiUriText } from './tui-uri-text.js';

export interface TuiResourcePreviewProps {
  content: TuiResourcePreviewContent;
  onOpenUri?: (uri: string) => void;
}

/** 单个资源正文预览。A single resource body preview. */
export function TuiResourcePreview({
  content,
  onOpenUri,
}: TuiResourcePreviewProps) {
  const metaLabel =
    content.meta?.filter(Boolean).join(' · ') || content.description;
  const target = content.navigationUri ?? content.uri;

  return (
    <div className="ov-tui__preview">
      {content.title ? (
        <div className="ov-tui__preview-head">
          <span className="ov-tui__card-title">{content.title}</span>
          {metaLabel ? (
            <span className="ov-tui__muted">{metaLabel}</span>
          ) : null}
        </div>
      ) : null}
      {content.uri ? (
        <button
          type="button"
          className="ov-tui__link ov-tui__preview-uri"
          onClick={() => target && onOpenUri?.(target)}
        >
          {content.uri}
        </button>
      ) : null}
      <pre className="ov-tui__preview-body">
        <TuiUriText text={content.content} onOpenUri={onOpenUri} />
      </pre>
    </div>
  );
}
