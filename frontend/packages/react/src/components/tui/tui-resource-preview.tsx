import type { TuiResourcePreviewContent } from './types.js';
import { TuiUriLink, TuiUriText } from './tui-uri-text.js';
import { TuiCodeBlock } from './tui-code-block.js';
import type { ResolvedTuiLabels } from './tui-labels.js';
import { isTuiJson } from './tui-result-text.js';

export interface TuiResourcePreviewProps {
  content: TuiResourcePreviewContent;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  labels: ResolvedTuiLabels;
}

/** 单个资源正文预览。A single resource body preview. */
export function TuiResourcePreview({
  content,
  onOpenUri,
  onCopy,
  labels,
}: TuiResourcePreviewProps) {
  const metaLabel =
    content.meta?.filter(Boolean).join(' · ') || content.description;
  const target = content.navigationUri ?? content.uri;

  return (
    <div className="ov-tui__preview">
      {metaLabel ? (
        <div className="ov-tui__result-meta">
          <TuiUriText text={metaLabel} mode="prose" onOpenUri={onOpenUri} />
        </div>
      ) : null}
      {content.title ? (
        <div className="ov-tui__card-title">
          <TuiUriText
            text={content.title}
            mode="literal"
            onOpenUri={onOpenUri}
          />
        </div>
      ) : null}
      <div className="ov-tui__preview-body">
        {content.uri ? (
          <TuiUriLink
            uri={target}
            onOpenUri={onOpenUri}
            className="ov-tui__link ov-tui__preview-uri"
          >
            {content.uri}
          </TuiUriLink>
        ) : null}
        {isTuiJson(content.content) ? (
          <TuiCodeBlock
            text={content.content}
            labels={labels}
            onCopy={onCopy}
            onOpenUri={onOpenUri}
          />
        ) : (
          <pre className="ov-tui__preview-content">
            <TuiUriText
              text={content.content}
              mode="literal"
              onOpenUri={onOpenUri}
            />
          </pre>
        )}
      </div>
    </div>
  );
}
